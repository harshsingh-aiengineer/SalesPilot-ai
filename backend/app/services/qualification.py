"""Deterministic qualification rules plus optional local-model interpretation.

Budget, measurements, and deadlines are accepted only when the source text
contains evidence. Missing facts stay unknown.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Lead
from app.schemas import QualificationOut
from app.services.leads import _activity, get_lead
from app.timeutils import utcnow

MEASUREMENT_TERMS = ("mm", "cm", "meter", "metre", "width", "height", "measurement", "sqm", "sq ft", "square")
URGENCY_HIGH = ("urgent", "asap", "immediately", "this week", "tomorrow")
URGENCY_MEDIUM = ("quotation", "quote", "pricing", "next month", "proposal")


@dataclass
class SourceFacts:
    text: str
    digits: str


def source_facts(*parts: str | None) -> SourceFacts:
    text = "\n".join(part for part in parts if part)
    return SourceFacts(text=text, digits=re.sub(r"[^\d]", "", text))


def _mentions_number(facts: SourceFacts, value: float | None) -> bool:
    if value is None:
        return False
    digits = re.sub(r"[^\d]", "", f"{value:.2f}".rstrip("0").rstrip("."))
    if not digits:
        return False
    return digits in facts.digits


def _mentions_text(facts: SourceFacts, value: str | None) -> bool:
    if not value:
        return False
    needle = value.strip().lower()
    if len(needle) < 3:
        return False
    return needle in facts.text.lower()


def rule_qualify(lead: Lead, extra_text: str | None = None) -> QualificationOut:
    facts = source_facts(lead.requirements, lead.notes, lead.location, extra_text)
    lowered = facts.text.lower()
    missing: list[str] = []
    uncertainty: list[str] = []
    score = 15

    has_contact = bool((lead.email or "").strip() or (lead.phone or "").strip())
    if has_contact:
        score += 15
    else:
        missing.append("contact")

    if (lead.requirements or "").strip():
        score += 25
    else:
        missing.append("requirements")

    if (lead.location or "").strip():
        score += 10
    else:
        missing.append("location")

    has_budget = lead.budget_min is not None or lead.budget_max is not None
    if has_budget:
        score += 15
    else:
        missing.append("budget")
        uncertainty.append("Budget was not provided.")

    if any(term in lowered for term in MEASUREMENT_TERMS):
        score += 10
    else:
        missing.append("measurements")
        uncertainty.append("Measurements were not provided.")

    urgency = "unknown"
    if any(term in lowered for term in URGENCY_HIGH):
        urgency = "high"
        score += 15
    elif any(term in lowered for term in URGENCY_MEDIUM):
        urgency = "medium"
        score += 8
    else:
        missing.append("deadline")
        uncertainty.append("No delivery or installation date was stated.")

    score = max(0, min(100, score))
    if has_contact and (lead.requirements or "").strip() and (has_budget or urgency == "high") and score >= 70:
        priority = "hot"
    elif score >= 40 and (lead.requirements or "").strip():
        priority = "warm"
    else:
        priority = "cold"

    known = 5 - len([item for item in ("contact", "requirements", "location", "budget", "measurements") if item in missing])
    confidence = max(0.2, min(0.9, 0.25 + known * 0.12))
    if "requirements" in missing:
        action = "Ask what product they need and where it will be installed."
    elif "measurements" in missing or "budget" in missing:
        action = "Prepare a clarification email for the missing measurements or budget."
    elif priority == "hot":
        action = "Prepare a quotation and schedule a follow-up."
    else:
        action = "Review the enquiry and decide whether to nurture or close it."

    explanation = (
        f"Rules scored this lead {score}/100 ({priority}). "
        f"Contact={'yes' if has_contact else 'no'}, "
        f"requirements={'yes' if 'requirements' not in missing else 'no'}, "
        f"budget={'yes' if has_budget else 'unknown'}, "
        f"urgency={urgency}."
    )
    return QualificationOut(
        score=score,
        priority=priority,  # type: ignore[arg-type]
        explanation=explanation,
        missing_fields=missing,
        recommended_action=action,
        confidence=round(confidence, 2),
        intent="sales_enquiry" if (lead.requirements or "").strip() else "unclear",
        requirements=(lead.requirements or None),
        location=lead.location,
        budget_min=lead.budget_min,
        budget_max=lead.budget_max,
        currency=lead.currency if has_budget else None,
        deadline=None,
        urgency=urgency,
        uncertainty_notes=uncertainty,
        method="rules",
    )


def apply_llm_interpretation(base: QualificationOut, llm_payload: dict, facts: SourceFacts) -> QualificationOut:
    """Merge model output without accepting invented commercial facts."""
    notes = list(base.uncertainty_notes)
    rejected: list[str] = []

    def keep_text(field: str) -> str | None:
        value = llm_payload.get(field)
        if value in (None, "", "unknown", "null"):
            return None
        if not isinstance(value, str):
            rejected.append(field)
            return None
        if _mentions_text(facts, value):
            return value.strip()
        rejected.append(field)
        notes.append(f"Ignored {field} because it was not found in the source text.")
        return None

    budget_min = llm_payload.get("budget_min")
    budget_max = llm_payload.get("budget_max")
    accepted_min = base.budget_min
    accepted_max = base.budget_max
    for label, raw in (("budget_min", budget_min), ("budget_max", budget_max)):
        if raw in (None, "", "unknown"):
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError):
            rejected.append(label)
            continue
        if _mentions_number(facts, number):
            if label == "budget_min":
                accepted_min = number
            else:
                accepted_max = number
        else:
            rejected.append(label)
            notes.append(f"Ignored {label} because that amount does not appear in the source text.")

    deadline = keep_text("deadline")
    location = keep_text("location") or base.location
    requirements = keep_text("requirements") or base.requirements
    intent = llm_payload.get("intent") if isinstance(llm_payload.get("intent"), str) else base.intent
    if isinstance(intent, str) and intent.lower() in {"not_sales", "newsletter", "spam", "unrelated"}:
        intent = intent.lower()

    missing = list(base.missing_fields)
    model_missing = llm_payload.get("missing_fields") or llm_payload.get("missing_information") or []
    if isinstance(model_missing, list):
        for item in model_missing:
            if isinstance(item, str) and item and item not in missing:
                missing.append(item[:80])

    explanation = base.explanation
    model_explanation = llm_payload.get("explanation") or llm_payload.get("rationale")
    if isinstance(model_explanation, str) and model_explanation.strip():
        explanation = model_explanation.strip()[:1200]

    action = base.recommended_action
    model_action = llm_payload.get("recommended_action")
    if isinstance(model_action, str) and model_action.strip():
        action = model_action.strip()[:500]

    try:
        model_confidence = float(llm_payload.get("confidence", base.confidence))
    except (TypeError, ValueError):
        model_confidence = base.confidence
    confidence = max(0.05, min(base.confidence, model_confidence if 0 <= model_confidence <= 1 else base.confidence))

    priority = base.priority
    # The model may lower priority, but it cannot upgrade to hot when rules withheld that label.
    model_priority = llm_payload.get("priority")
    if model_priority in {"hot", "warm", "cold"}:
        rank = {"cold": 0, "warm": 1, "hot": 2}
        if rank[model_priority] <= rank[base.priority]:
            priority = model_priority

    try:
        model_score = int(llm_payload.get("score", base.score))
    except (TypeError, ValueError):
        model_score = base.score
    score = base.score
    if 0 <= model_score <= 100:
        # Keep the deterministic score as the ceiling when required facts are missing.
        score = min(base.score, model_score) if base.priority != "hot" else max(min(model_score, 100), min(base.score, 100))
        if "requirements" in base.missing_fields:
            score = min(score, base.score)

    if rejected:
        notes.append("Some model fields were rejected: " + ", ".join(sorted(set(rejected))) + ".")

    return QualificationOut(
        score=max(0, min(100, score)),
        priority=priority,  # type: ignore[arg-type]
        explanation=explanation,
        missing_fields=missing,
        recommended_action=action,
        confidence=round(confidence, 2),
        intent=intent if isinstance(intent, str) else base.intent,
        requirements=requirements,
        location=location,
        budget_min=accepted_min,
        budget_max=accepted_max,
        currency=base.currency or (llm_payload.get("currency") if isinstance(llm_payload.get("currency"), str) else None),
        deadline=deadline,
        urgency=base.urgency,
        uncertainty_notes=notes,
        method="hybrid",
    )


def persist_qualification(db: Session, lead: Lead, result: QualificationOut, actor: str = "system") -> Lead:
    lead.qualification_score = result.score
    lead.qualification_summary = result.explanation
    lead.qualification_confidence = result.confidence
    lead.qualification_method = result.method
    lead.missing_information = json.dumps(result.missing_fields)
    lead.priority = result.priority
    lead.next_action = result.recommended_action
    if result.requirements and not lead.requirements:
        lead.requirements = result.requirements
    if result.location and not lead.location:
        lead.location = result.location
    if lead.budget_min is None and result.budget_min is not None:
        lead.budget_min = result.budget_min
    if lead.budget_max is None and result.budget_max is not None:
        lead.budget_max = result.budget_max
    lead.updated_at = utcnow()
    _activity(
        db,
        lead.id,
        "qualified",
        f"Qualified {result.score}/100 ({result.priority}) via {result.method}",
        {"missing": result.missing_fields, "confidence": result.confidence},
    )
    from app.services.audit import write_audit

    write_audit(
        db,
        actor_type=actor,
        action="lead.qualify",
        entity_type="lead",
        entity_id=lead.id,
        result="ok",
        details={"score": result.score, "priority": result.priority, "method": result.method},
    )
    return lead


def qualify_lead_record(db: Session, lead_id: int, llm_payload: dict | None = None, extra_text: str | None = None, actor: str = "system") -> QualificationOut:
    lead = get_lead(db, lead_id)
    result = rule_qualify(lead, extra_text=extra_text)
    if llm_payload:
        facts = source_facts(lead.requirements, lead.notes, lead.location, extra_text, lead.name)
        result = apply_llm_interpretation(result, llm_payload, facts)
    persist_qualification(db, lead, result, actor=actor)
    return result
