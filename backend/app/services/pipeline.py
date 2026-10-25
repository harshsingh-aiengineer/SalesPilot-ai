"""Email classification, lead linking, drafts, and guarded sends."""

from __future__ import annotations

import json
import re
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import get_settings
from app.gmail.client import GmailApiError, GmailClient, build_raw_message
from app.gmail.parser import extract_display_name, extract_email_address
from app.llm import LlmError, LlmLock, chat
from app.models import EmailDraft, EmailMessage, Lead
from app.schemas import LeadCreate
from app.services.audit import write_audit
from app.services.leads import _activity, create_lead, find_lead_by_email
from app.services.qualification import (
    apply_llm_interpretation,
    persist_qualification,
    rule_qualify,
    source_facts,
)
from app.services.settings_store import get_settings_row
from app.timeutils import utcnow

PRODUCT_TERMS = (
    "blind",
    "blinds",
    "curtain",
    "curtains",
    "shade",
    "shutter",
    "install",
    "installation",
    "quotation",
    "quote",
    "motorized",
    "blackout",
)
BUDGET_RE = re.compile(
    r"(?:(?:aed|usd|\$|د\.إ)\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:aed|usd))",
    re.I,
)


class MailFlowError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def _budget_from_text(text: str) -> tuple[float | None, str | None]:
    match = BUDGET_RE.search(text or "")
    if not match:
        return None, None
    raw = match.group(1) or match.group(2)
    currency = "USD" if "$" in match.group(0) or "usd" in match.group(0).lower() else "AED"
    try:
        return float(raw.replace(",", "")), currency
    except ValueError:
        return None, None


def classify_email_rules(email: EmailMessage) -> dict:
    text = f"{email.subject or ''}\n{email.body_text or ''}"
    lowered = text.lower()
    address = extract_email_address(email.sender)
    name = extract_display_name(email.sender)
    product_hit = any(term in lowered for term in PRODUCT_TERMS)
    budget, currency = _budget_from_text(text)
    missing = []
    if not any(term in lowered for term in ("mm", "cm", "meter", "metre", "width", "height", "measurement")):
        missing.append("measurements")
    if budget is None:
        missing.append("budget")
    if not any(term in lowered for term in ("urgent", "asap", "tomorrow", "this week", "next week", "date")):
        missing.append("deadline")
    location = None
    for token in ("dubai", "marina", "jumeirah", "business bay", "downtown", "deira", "abu dhabi", "sharjah"):
        if token in lowered:
            location = token.title()
            break
    is_sales = bool(product_hit and address and email.direction == "inbound")
    confidence = 0.8 if is_sales else (0.45 if product_hit else 0.15)
    if email.direction == "outbound":
        is_sales = False
        confidence = 0.95
    return {
        "is_sales_enquiry": is_sales,
        "confidence": confidence,
        "name": name,
        "email": address,
        "requirements": ((email.subject or "").strip() or None) if product_hit else None,
        "location": location,
        "budget_min": budget,
        "budget_max": budget,
        "currency": currency,
        "missing_fields": missing,
        "recommended_action": "Ask for the missing details before quoting." if missing else "Review and prepare a quotation.",
        "rationale": "Matched product language and a sender address." if is_sales else "Not enough evidence of a sales enquiry.",
        "method": "rules",
    }


def _llm_json(db: Session, prompt: str, holder: str) -> dict:
    with LlmLock(db, holder=holder):
        payload = chat(
            [
                {
                    "role": "system",
                    "content": "Return only a JSON object. Use null for unknown facts. Never invent budgets, measurements, or dates.",
                },
                {"role": "user", "content": prompt},
            ],
            force_json=True,
        )
    content = ((payload.get("message") or {}).get("content") or "").strip()
    if content.startswith("```"):
        content = content.strip("`")
        content = content.removeprefix("json").strip()
    return json.loads(content)


def extract_email(db: Session, email: EmailMessage, *, use_llm: bool = True) -> dict:
    rules = classify_email_rules(email)
    if not use_llm:
        return rules
    prompt = (
        "Decide whether this email is from a person or business asking to buy, get a price or quote, "
        "order, or learn about our products or services. The wording can be anything "
        "(sales, sale, salespilot, enquiry, price, order, need, interested, and so on). "
        "Newsletters, job alerts, bank or marketing mail, receipts, and automated notifications are not sales enquiries.\n"
        "Then extract only facts written in the email.\n"
        "Keys: is_sales_enquiry (bool), confidence (0-1), name, requirements, location, "
        "budget_min, budget_max, currency, deadline, urgency, missing_fields (array), "
        "recommended_action, rationale.\n\n"
        f"From: {email.sender}\nSubject: {email.subject}\n\n{(email.body_text or '')[:3500]}"
    )
    try:
        parsed = _llm_json(db, prompt, holder=f"email-{email.id}")
    except (LlmError, json.JSONDecodeError, TypeError, ValueError) as exc:
        rules["llm_error"] = getattr(exc, "message", None) or f"Model output could not be used ({exc.__class__.__name__})."
        return rules
    if not isinstance(parsed, dict):
        rules["llm_error"] = "Model output was not a JSON object."
        return rules
    facts = source_facts(email.subject, email.body_text, email.sender)
    # Reuse qualification evidence checks by adapting fields.
    from app.services.qualification import _mentions_number, _mentions_text

    merged = dict(rules)
    merged["method"] = "hybrid"
    can_be_lead = email.direction == "inbound" and bool(rules.get("email"))
    if isinstance(parsed.get("is_sales_enquiry"), bool) and can_be_lead:
        merged["is_sales_enquiry"] = parsed["is_sales_enquiry"]
        if not parsed["is_sales_enquiry"]:
            merged["rationale"] = str(parsed.get("rationale") or "Model classified this as not a sales enquiry.")
    elif not can_be_lead:
        merged["is_sales_enquiry"] = False
    try:
        model_conf = float(parsed.get("confidence", rules["confidence"]))
        if 0 <= model_conf <= 1:
            merged["confidence"] = round(max(0.0, min(1.0, model_conf)), 2)
    except (TypeError, ValueError):
        pass
    for field in ("requirements", "location", "deadline"):
        value = parsed.get(field)
        if isinstance(value, str) and _mentions_text(facts, value):
            merged[field] = value.strip()
    for field in ("budget_min", "budget_max"):
        raw = parsed.get(field)
        try:
            number = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            number = None
        if number is not None and _mentions_number(facts, number):
            merged[field] = number
    if isinstance(parsed.get("missing_fields"), list):
        merged["missing_fields"] = [str(item)[:80] for item in parsed["missing_fields"] if str(item).strip()][:12]
    if isinstance(parsed.get("recommended_action"), str):
        merged["recommended_action"] = parsed["recommended_action"][:500]
    if isinstance(parsed.get("rationale"), str):
        merged["rationale"] = parsed["rationale"][:800]
    if isinstance(parsed.get("name"), str) and _mentions_text(facts, parsed["name"]):
        merged["name"] = parsed["name"].strip()
    return merged


_FORM_LINE = re.compile(
    r"^(name|phone|mobile|company|location|requirements|requirement|budget|deadline)\s*[:\-]\s*(.*)$",
    re.I,
)
_EMPTY_FORM_VALUE = {"", "-", "—", "n/a", "na", "none", "nil"}


def parse_detail_form(text: str) -> dict[str, str]:
    """Read the labeled lines we ask the customer to send back."""
    found: dict[str, str] = {}
    for raw_line in (text or "").splitlines():
        match = _FORM_LINE.match(raw_line.strip())
        if not match:
            continue
        key = match.group(1).lower()
        if key == "mobile":
            key = "phone"
        if key == "requirement":
            key = "requirements"
        value = match.group(2).strip()
        if value.lower() in _EMPTY_FORM_VALUE or key in found:
            continue
        found[key] = value[:500]
    return found


def _budget_span(text: str) -> tuple[float | None, float | None, str | None]:
    amount, currency = _budget_from_text(text)
    numbers = []
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            numbers.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    if not numbers and amount is None:
        return None, None, currency
    if amount is not None and not numbers:
        numbers = [amount]
    low, high = min(numbers), max(numbers)
    return low, high, currency or "AED"


def apply_customer_form(extraction: dict, form: dict[str, str]) -> dict:
    if not form:
        return extraction
    merged = dict(extraction)
    merged["form"] = form
    merged["is_sales_enquiry"] = True
    merged["confidence"] = max(float(merged.get("confidence") or 0), 0.9)
    merged["method"] = "customer_form"
    for key in ("name", "phone", "company", "location", "requirements", "deadline"):
        if form.get(key):
            merged[key] = form[key]
    if form.get("budget"):
        low, high, currency = _budget_span(form["budget"])
        if low is not None:
            merged["budget_min"] = low
            merged["budget_max"] = high
            merged["currency"] = currency or merged.get("currency") or "AED"
    return merged


def _put_deadline(lead: Lead, deadline: str | None, *, overwrite: bool) -> None:
    if not deadline:
        return
    line = f"Deadline: {deadline.strip()[:200]}"
    notes = lead.notes or ""
    if re.search(r"(?m)^Deadline:", notes):
        if overwrite:
            lead.notes = re.sub(r"(?m)^Deadline:.*$", line, notes)
        return
    lead.notes = (notes.rstrip() + "\n" + line).strip()


def apply_customer_fields(lead: Lead, extraction: dict) -> None:
    form = extraction.get("form") or {}

    def take(field: str, value, limit: int) -> None:
        if value is None or str(value).strip() == "":
            return
        if form.get(field) or not getattr(lead, field):
            setattr(lead, field, str(value).strip()[:limit])

    take("phone", extraction.get("phone"), 50)
    take("company", extraction.get("company"), 200)
    take("location", extraction.get("location"), 200)
    take("requirements", extraction.get("requirements"), 2000)
    if extraction.get("name") and (form.get("name") or not lead.name):
        lead.name = str(extraction["name"]).strip()[:200]
    if form.get("budget") or (lead.budget_min is None and extraction.get("budget_min") is not None):
        if extraction.get("budget_min") is not None:
            lead.budget_min = extraction.get("budget_min")
            lead.budget_max = extraction.get("budget_max")
        if extraction.get("currency"):
            lead.currency = str(extraction["currency"])[:8]
    _put_deadline(lead, extraction.get("deadline"), overwrite=bool(form.get("deadline")))
    lead.updated_at = utcnow()


def missing_customer_details(lead: Lead) -> list[str]:
    missing = []
    if not (lead.phone or "").strip():
        missing.append("phone")
    if not (lead.company or "").strip():
        missing.append("company")
    if not (lead.location or "").strip():
        missing.append("location")
    if not (lead.requirements or "").strip():
        missing.append("requirements")
    if lead.budget_min is None and lead.budget_max is None:
        missing.append("budget")
    if not re.search(r"(?m)^Deadline:", lead.notes or ""):
        missing.append("deadline")
    return missing


def _requirements_text(email: EmailMessage, extraction: dict) -> str | None:
    if extraction.get("requirements"):
        return str(extraction["requirements"])[:2000]
    body = (email.body_text or "").strip()
    subject = (email.subject or "").strip()
    if body:
        return (subject + "\n" + body)[:2000]
    return subject or None


def upsert_lead_from_email(db: Session, email: EmailMessage, extraction: dict) -> tuple[Lead | None, str]:
    settings = get_settings_row(db)
    if not settings.lead_creation_enabled:
        return None, "lead_creation_disabled"
    if not extraction.get("is_sales_enquiry"):
        return None, "not_a_sales_enquiry"
    if float(extraction.get("confidence") or 0) < settings.qualification_confidence_threshold:
        return None, "below_confidence_threshold"
    address = extraction.get("email") or extract_email_address(email.sender)
    if not address:
        return None, "missing_sender_email"
    requirements = _requirements_text(email, extraction)
    if not requirements:
        return None, "missing_requirements"
    existing = find_lead_by_email(db, address)
    if existing is not None:
        if requirements and not existing.requirements and not extraction.get("form"):
            existing.requirements = requirements
        apply_customer_fields(existing, extraction)
        _activity(db, existing.id, "email_linked", f"Linked email {email.id} and updated customer details", {"email_id": email.id})
        email.lead_id = existing.id
        return existing, "updated_existing"
    name = extraction.get("name") or extract_display_name(email.sender) or address.split("@")[0]
    lead = create_lead(
        db,
        LeadCreate(
            name=name[:200],
            email=address,
            phone=(str(extraction["phone"])[:50] if extraction.get("phone") else None),
            company=(str(extraction["company"])[:200] if extraction.get("company") else None),
            location=extraction.get("location"),
            requirements=requirements,
            budget_min=extraction.get("budget_min"),
            budget_max=extraction.get("budget_max"),
            currency=extraction.get("currency") or "AED",
            source="gmail",
            status="new",
            priority="unset",
            notes=(f"Deadline: {extraction['deadline']}" if extraction.get("deadline") else None),
            is_synthetic=False,
        ),
        actor="worker",
    )
    email.lead_id = lead.id
    _activity(db, lead.id, "email_linked", f"Created from Gmail message {email.provider_message_id}")
    return lead, "created"


def _reply_subject(lead: Lead, reply_subject: str | None) -> str:
    if reply_subject and reply_subject.strip():
        original = reply_subject.strip()
        subject = original if original.lower().startswith("re:") else f"Re: {original}"
    else:
        subject = f"Re: your enquiry — {lead.company or lead.name}"
    return subject[:180]


def _budget_line(lead: Lead) -> str:
    if lead.budget_min is None and lead.budget_max is None:
        return ""
    if lead.budget_min is not None and lead.budget_max is not None and lead.budget_min != lead.budget_max:
        return f"{lead.budget_min:g}-{lead.budget_max:g} {lead.currency}"
    amount = lead.budget_min if lead.budget_min is not None else lead.budget_max
    return f"{amount:g} {lead.currency}"


def _deadline_line(lead: Lead) -> str:
    match = re.search(r"(?m)^Deadline:\s*(.*)$", lead.notes or "")
    return match.group(1).strip() if match else ""


def render_detail_request(lead: Lead, reply_subject: str | None = None) -> tuple[str, str]:
    body = "\n".join(
        [
            f"Hello {lead.name},",
            "",
            "Thank you for your enquiry. We have started your lead.",
            "Please reply to this email and keep the labels below. Fill any blank line.",
            "",
            f"Name: {lead.name or ''}",
            f"Phone: {lead.phone or ''}",
            f"Company: {lead.company or ''}",
            f"Location: {lead.location or ''}",
            f"Requirements: {lead.requirements or ''}",
            f"Budget: {_budget_line(lead)}",
            f"Deadline: {_deadline_line(lead)}",
            "",
            "Reply with those seven lines filled in. Your reply is read automatically and the lead is updated.",
            "",
            "Regards,",
            "Sales team",
        ]
    )
    return _reply_subject(lead, reply_subject), body


def render_details_received(lead: Lead, reply_subject: str | None = None) -> tuple[str, str]:
    body = "\n".join(
        [
            f"Hello {lead.name},",
            "",
            "Thank you. We received your details and added them to your lead.",
            "",
            f"Name: {lead.name or ''}",
            f"Phone: {lead.phone or ''}",
            f"Company: {lead.company or ''}",
            f"Location: {lead.location or ''}",
            f"Requirements: {lead.requirements or ''}",
            f"Budget: {_budget_line(lead)}",
            f"Deadline: {_deadline_line(lead)}",
            "",
            "We will follow up with the next step.",
            "",
            "Regards,",
            "Sales team",
        ]
    )
    return _reply_subject(lead, reply_subject), body


def maybe_create_draft(
    db: Session,
    lead: Lead,
    missing: list[str],
    source_email_id: int | None = None,
    reply_subject: str | None = None,
) -> EmailDraft | None:
    settings = get_settings_row(db)
    if not settings.draft_generation_enabled:
        return None
    if not lead.email:
        return None
    cooldown_start = utcnow() - timedelta(hours=settings.follow_up_cooldown_hours)
    recent = (
        db.query(EmailDraft)
        .filter(EmailDraft.lead_id == lead.id, EmailDraft.created_at >= cooldown_start)
        .order_by(EmailDraft.id.desc())
        .first()
    )
    template = "detail_form" if missing else "details_received"
    if recent is not None and recent.template_name == template:
        return None
    if recent is not None and template == "detail_form":
        return None
    if missing:
        subject, body = render_detail_request(lead, reply_subject)
    else:
        subject, body = render_details_received(lead, reply_subject)
    llm_error = None
    draft = EmailDraft(
        lead_id=lead.id,
        source_email_id=source_email_id,
        recipient=lead.email,
        subject=subject,
        body=body,
        template_name=template,
        status="pending_approval",
        approval_state="pending",
        idempotency_key=f"auto-draft:{lead.id}:{source_email_id or 'followup'}:{template}:{utcnow().strftime('%Y%m%d%H')}",
    )
    db.add(draft)
    db.flush()
    _activity(
        db,
        lead.id,
        "draft_created",
        f"Draft {draft.id} saved for approval ({template})",
        {"llm_error": llm_error} if llm_error else {"template": template},
    )
    if llm_error:
        write_audit(
            db,
            actor_type="system",
            action="draft.llm_fallback",
            entity_type="email_draft",
            entity_id=draft.id,
            result="error",
            details={"error": llm_error},
        )
    return draft


def process_stored_email(db: Session, email_id: int, *, use_llm: bool = True) -> dict:
    email = db.get(EmailMessage, email_id)
    if email is None:
        raise MailFlowError("not_found", "Email not found", 404)
    if email.processing_status in {"processed", "ignored"} and email.processed_at is not None:
        return {
            "ok": True,
            "status": "already_processed",
            "email_id": email.id,
            "lead_id": email.lead_id,
            "processing_status": email.processing_status,
        }
    if email.direction == "outbound":
        email.processing_status = "ignored"
        email.classification = "outbound"
        email.processed_at = utcnow()
        return {"ok": True, "status": "ignored_outbound", "email_id": email.id}
    extraction = extract_email(db, email, use_llm=use_llm)
    extraction = apply_customer_form(extraction, parse_detail_form(email.body_text or ""))
    email.extraction_json = json.dumps(extraction, default=str)[:8000]
    email.classification = "sales_enquiry" if extraction.get("is_sales_enquiry") else "not_sales"
    lead, reason = upsert_lead_from_email(db, email, extraction)
    draft_id = None
    if lead is not None:
        result = rule_qualify(lead, extra_text=email.body_text)
        if extraction.get("method") == "hybrid":
            facts = source_facts(lead.requirements, lead.notes, email.body_text, email.subject)
            result = apply_llm_interpretation(
                result,
                {
                    "explanation": extraction.get("rationale"),
                    "missing_fields": extraction.get("missing_fields"),
                    "recommended_action": extraction.get("recommended_action"),
                    "confidence": extraction.get("confidence"),
                    "location": extraction.get("location"),
                    "requirements": extraction.get("requirements"),
                    "budget_min": extraction.get("budget_min"),
                    "budget_max": extraction.get("budget_max"),
                    "priority": "warm",
                },
                facts,
            )
        persist_qualification(db, lead, result, actor="worker")
        draft = maybe_create_draft(
            db,
            lead,
            missing_customer_details(lead),
            source_email_id=email.id,
            reply_subject=email.subject,
        )
        draft_id = draft.id if draft else None
        if draft is not None:
            queue_auto_reply(db, draft)
        email.processing_status = "processed"
    elif reason in {"below_confidence_threshold", "missing_requirements", "missing_sender_email"} and extraction.get("is_sales_enquiry"):
        email.processing_status = "needs_review"
    elif reason == "lead_creation_disabled" and extraction.get("is_sales_enquiry"):
        email.processing_status = "needs_review"
    else:
        email.processing_status = "processed"
        email.classification = "not_sales"
    email.processed_at = utcnow()
    write_audit(
        db,
        actor_type="worker",
        action="email.process",
        entity_type="email_message",
        entity_id=email.id,
        result="ok",
        details={
            "classification": email.classification,
            "reason": reason,
            "lead_id": lead.id if lead else None,
            "draft_id": draft_id,
            "llm_error": extraction.get("llm_error"),
        },
    )
    return {
        "ok": True,
        "status": email.processing_status,
        "email_id": email.id,
        "lead_id": lead.id if lead else None,
        "link_result": reason,
        "draft_id": draft_id,
        "extraction": extraction,
    }


def queue_auto_reply(db: Session, draft: EmailDraft) -> bool:
    settings = get_settings_row(db)
    if not settings.auto_send_enabled or settings.send_requires_approval:
        return False
    permitted, _reason = check_recipient(db, draft.recipient, user_confirmed=False)
    if not permitted:
        return False
    from app.services.tasks import enqueue

    draft.approval_state = "approved"
    draft.approved_at = utcnow()
    draft.status = "approved"
    enqueue(
        db,
        task_type="send_email",
        payload={"draft_id": draft.id},
        idempotency_key=f"auto_reply:{draft.id}",
        priority=3,
    )
    if draft.lead_id:
        _activity(db, draft.lead_id, "auto_reply_queued", f"Automatic reply {draft.id} queued for sending")
    return True


def _allowlist(settings) -> list[str]:
    return [item.strip().lower() for item in (settings.allowed_recipient_scope or "").split(",") if item.strip()]


def check_recipient(db: Session, recipient: str, *, user_confirmed: bool) -> tuple[bool, str]:
    settings = get_settings_row(db)
    address = recipient.strip().lower()
    allow = _allowlist(settings)
    test_recipient = get_settings().gmail_test_recipient.strip().lower()
    domain = address.split("@")[-1] if "@" in address else ""
    allowed = (
        "*" in allow
        or address in allow
        or domain in allow
        or (domain and f"@{domain}" in allow)
        or (test_recipient and address == test_recipient)
    )
    if user_confirmed:
        return True, "user_confirmed"
    if not settings.auto_send_enabled:
        return False, "automatic_sending_is_disabled"
    if not allowed:
        return False, "recipient_is_outside_the_allowed_scope"
    return True, "allowlist"


def _rate_limit_ok(db: Session) -> tuple[bool, str]:
    settings = get_settings_row(db)
    now = utcnow()
    hour_ago = now - timedelta(hours=1)
    day_ago = now - timedelta(days=1)
    sent = db.query(EmailDraft).filter(EmailDraft.status == "provider_accepted", EmailDraft.sent_at.is_not(None))
    per_hour = sent.filter(EmailDraft.sent_at >= hour_ago).count()
    per_day = sent.filter(EmailDraft.sent_at >= day_ago).count()
    if per_hour >= settings.max_emails_per_hour:
        return False, f"Hourly send limit reached ({settings.max_emails_per_hour})."
    if per_day >= settings.max_emails_per_day:
        return False, f"Daily send limit reached ({settings.max_emails_per_day})."
    return True, "ok"


def reconcile_send(db: Session, draft: EmailDraft, client: GmailClient | None = None) -> dict:
    if draft.provider_message_id and draft.status == "provider_accepted":
        return {"ok": True, "status": "provider_accepted", "provider_message_id": draft.provider_message_id, "reconciled": True}
    gmail = client or GmailClient()
    try:
        matches = gmail.list_sent_matching(draft.recipient, draft.subject)
    except GmailApiError as exc:
        return {"ok": False, "status": draft.status, "error": exc.message, "code": exc.code}
    for item in matches:
        message_id = item.get("id")
        if not message_id:
            continue
        full = gmail.get_message(message_id)
        headers = {
            str(header.get("name") or "").lower(): header.get("value")
            for header in ((full.get("payload") or {}).get("headers") or [])
        }
        if str(headers.get("x-salespilot-draft") or "") == str(draft.id):
            draft.status = "provider_accepted"
            draft.provider_message_id = message_id
            draft.sent_at = draft.sent_at or utcnow()
            draft.send_error = None
            draft.updated_at = utcnow()
            return {"ok": True, "status": "provider_accepted", "provider_message_id": message_id, "reconciled": True}
    return {"ok": True, "status": "not_found", "provider_message_id": None, "reconciled": False}


def send_draft(
    db: Session,
    draft_id: int,
    *,
    user_confirmed: bool,
    expected_recipient: str | None = None,
    actor: str = "user",
    client: GmailClient | None = None,
) -> dict:
    draft = db.get(EmailDraft, draft_id)
    if draft is None:
        raise MailFlowError("not_found", "Draft not found", 404)
    if expected_recipient and expected_recipient.strip().lower() != draft.recipient.strip().lower():
        raise MailFlowError("recipient_mismatch", "The confirmed recipient does not match the draft.", 409)
    if draft.status == "provider_accepted" and draft.provider_message_id:
        return {
            "ok": True,
            "status": "provider_accepted",
            "provider_message_id": draft.provider_message_id,
            "already_sent": True,
        }
    if draft.status == "send_unknown":
        reconciled = reconcile_send(db, draft, client=client)
        if reconciled.get("status") == "provider_accepted":
            return reconciled
        raise MailFlowError(
            "send_outcome_unknown",
            "A previous send attempt did not return a result. Reconcile it before sending again.",
            409,
        )
    if not user_confirmed and draft.approval_state != "approved":
        raise MailFlowError("approval_required", "This draft has not been approved for sending.", 403)
    settings = get_settings_row(db)
    if user_confirmed and settings.send_requires_approval:
        draft.approval_state = "approved"
        draft.approved_at = utcnow()
    permitted, reason = check_recipient(db, draft.recipient, user_confirmed=user_confirmed)
    if not permitted:
        raise MailFlowError("send_blocked", f"Send blocked: {reason.replace('_', ' ')}.", 403)
    ok_rate, rate_message = _rate_limit_ok(db)
    if not ok_rate:
        raise MailFlowError("rate_limited", rate_message, 429)
    if not draft.recipient or "@" not in draft.recipient:
        raise MailFlowError("invalid_recipient", "The draft does not have a valid recipient.", 400)
    if not draft.subject.strip() or not draft.body.strip():
        raise MailFlowError("empty_draft", "Subject and body are required before sending.", 400)

    draft.status = "send_attempt"
    draft.updated_at = utcnow()
    db.commit()

    profile_email = None
    try:
        gmail = client or GmailClient()
        if client is None:
            profile_email = gmail.get_profile().get("emailAddress")
        raw = build_raw_message(
            sender=profile_email,
            recipient=draft.recipient,
            subject=draft.subject,
            body=draft.body,
            draft_id=draft.id,
        )
        sent = gmail.send_raw(raw)
    except GmailApiError as exc:
        if exc.code in {"gmail_quota"}:
            draft.status = "send_failed"
        elif exc.code in {"gmail_unauthorized", "not_authorized", "credentials_invalid", "refresh_failed"}:
            draft.status = "send_failed"
        else:
            draft.status = "send_unknown" if exc.status >= 500 else "send_failed"
        draft.send_error = exc.message
        draft.updated_at = utcnow()
        write_audit(
            db,
            actor_type=actor,
            action="email.send",
            entity_type="email_draft",
            entity_id=draft.id,
            result="error",
            details={"code": exc.code, "status": draft.status},
        )
        raise MailFlowError(exc.code, exc.message, exc.status) from exc
    except Exception as exc:
        draft.status = "send_unknown"
        draft.send_error = f"Send result is unknown after {exc.__class__.__name__}."
        draft.updated_at = utcnow()
        write_audit(
            db,
            actor_type=actor,
            action="email.send",
            entity_type="email_draft",
            entity_id=draft.id,
            result="unknown",
            details={"error": draft.send_error},
        )
        raise MailFlowError("send_unknown", draft.send_error, 504) from exc

    provider_id = sent.get("id")
    if not provider_id:
        draft.status = "send_unknown"
        draft.send_error = "Gmail did not return a message id."
        draft.updated_at = utcnow()
        raise MailFlowError("send_unknown", draft.send_error, 502)
    draft.status = "provider_accepted"
    draft.provider_message_id = provider_id
    draft.sent_at = utcnow()
    draft.send_error = None
    draft.updated_at = utcnow()
    if draft.lead_id:
        _activity(db, draft.lead_id, "email_sent", f"Gmail accepted message {provider_id}")
    write_audit(
        db,
        actor_type=actor,
        action="email.send",
        entity_type="email_draft",
        entity_id=draft.id,
        result="ok",
        details={"provider_message_id": provider_id, "recipient": draft.recipient},
    )
    return {"ok": True, "status": "provider_accepted", "provider_message_id": provider_id, "already_sent": False}


def create_gmail_draft(db: Session, draft_id: int, client: GmailClient | None = None) -> dict:
    draft = db.get(EmailDraft, draft_id)
    if draft is None:
        raise MailFlowError("not_found", "Draft not found", 404)
    gmail = client or GmailClient()
    profile = gmail.get_profile()
    raw = build_raw_message(
        sender=profile.get("emailAddress"),
        recipient=draft.recipient,
        subject=draft.subject,
        body=draft.body,
        draft_id=draft.id,
    )
    created = gmail.create_draft(raw)
    provider_draft_id = created.get("id")
    if not provider_draft_id:
        raise MailFlowError("gmail_draft_failed", "Gmail did not return a draft id.", 502)
    draft.provider_draft_id = provider_draft_id
    if draft.status == "local_draft":
        draft.status = "gmail_draft"
    draft.updated_at = utcnow()
    write_audit(
        db,
        actor_type="user",
        action="email.gmail_draft",
        entity_type="email_draft",
        entity_id=draft.id,
        result="ok",
        details={"provider_draft_id": provider_draft_id},
    )
    return {"ok": True, "status": draft.status, "provider_draft_id": provider_draft_id}
