"""Gmail connection, inbox, and drafts."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import require_user
from app.config import get_settings
from app.database import get_db
from app.gmail.client import GmailApiError
from app.gmail.oauth import GmailAuthError, authorization_url, disconnect, exchange_code
from app.gmail.sync import connection_status, sync_inbox
from app.models import EmailDraft, EmailMessage, Lead
from app.schemas import DraftCreate, DraftOut, DraftUpdate, EmailList, EmailOut, LinkLeadRequest, SendRequest
from app.services.audit import write_audit
from app.services.leads import LeadError, get_lead
from app.services.pipeline import MailFlowError, create_gmail_draft, process_stored_email, reconcile_send, send_draft
from app.services.tasks import enqueue
from app.timeutils import utcnow

router = APIRouter()


def _mail_error(exc: MailFlowError | GmailApiError | GmailAuthError) -> None:
    raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from exc


@router.get("/gmail/status")
def gmail_status(_: str = Depends(require_user)) -> dict:
    return connection_status()


@router.post("/gmail/authorize")
def gmail_authorize(db: Session = Depends(get_db), _: str = Depends(require_user)) -> dict:
    try:
        url = authorization_url(db)
    except GmailAuthError as exc:
        _mail_error(exc)
    return {"authorization_url": url}


@router.get("/gmail/callback")
def gmail_callback(code: str | None = None, state: str | None = None, error: str | None = None, db: Session = Depends(get_db)):
    settings = get_settings()
    target = settings.frontend_base_url.rstrip("/") + "/settings"
    if error or not code or not state:
        return RedirectResponse(f"{target}?gmail=error&reason={quote(error or 'missing_code')}")
    try:
        exchange_code(db, code, state)
    except GmailAuthError as exc:
        return RedirectResponse(f"{target}?gmail=error&reason={quote(exc.code)}")
    status = connection_status()
    if not status.get("connected"):
        return RedirectResponse(f"{target}?gmail=error&reason={quote(status.get('reason') or 'profile_check_failed')}")
    return RedirectResponse(f"{target}?gmail=connected")


@router.post("/gmail/disconnect")
def gmail_disconnect(db: Session = Depends(get_db), _: str = Depends(require_user)) -> dict:
    disconnect()
    write_audit(db, actor_type="user", action="gmail.disconnect", entity_type="gmail", entity_id=None, result="ok", details={})
    db.commit()
    return connection_status()


@router.post("/gmail/sync")
def gmail_sync(
    max_results: int = Query(default=15, ge=1, le=50),
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
) -> dict:
    try:
        result = sync_inbox(db, max_results=max_results)
    except GmailApiError as exc:
        _mail_error(exc)
    db.commit()
    return result


@router.get("/emails", response_model=EmailList)
def list_emails(
    q: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
) -> EmailList:
    query = db.query(EmailMessage)
    if status:
        query = query.filter(EmailMessage.processing_status == status)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter((EmailMessage.subject.ilike(like)) | (EmailMessage.sender.ilike(like)))
    total = query.count()
    rows = query.order_by(EmailMessage.received_at.desc().nulls_last(), EmailMessage.id.desc()).offset(offset).limit(limit).all()
    return EmailList(items=[EmailOut.model_validate(row) for row in rows], total=total)


@router.get("/emails/{email_id}", response_model=EmailOut)
def get_email(email_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)) -> EmailOut:
    row = db.get(EmailMessage, email_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Email not found."})
    return EmailOut.model_validate(row)


@router.post("/emails/{email_id}/process")
def process_email(email_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)) -> dict:
    try:
        result = process_stored_email(db, email_id, use_llm=True)
    except MailFlowError as exc:
        _mail_error(exc)
    db.commit()
    return result


@router.post("/emails/{email_id}/link")
def link_email(email_id: int, body: LinkLeadRequest, db: Session = Depends(get_db), _: str = Depends(require_user)) -> dict:
    row = db.get(EmailMessage, email_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Email not found."})
    if body.create:
        result = process_stored_email(db, email_id, use_llm=True)
        db.commit()
        return result
    if body.lead_id is None:
        raise HTTPException(status_code=400, detail={"code": "lead_required", "message": "Provide lead_id or set create=true."})
    try:
        lead = get_lead(db, body.lead_id)
    except LeadError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from exc
    row.lead_id = lead.id
    row.processing_status = "processed"
    row.processed_at = utcnow()
    db.commit()
    return {"ok": True, "email_id": row.id, "lead_id": lead.id}


@router.get("/drafts", response_model=list[DraftOut])
def list_drafts(
    q: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
) -> list[DraftOut]:
    query = db.query(EmailDraft)
    if status:
        query = query.filter(EmailDraft.status == status)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter((EmailDraft.subject.ilike(like)) | (EmailDraft.recipient.ilike(like)) | (EmailDraft.body.ilike(like)))
    rows = query.order_by(EmailDraft.updated_at.desc()).limit(200).all()
    return [DraftOut.model_validate(row) for row in rows]


@router.post("/drafts", response_model=DraftOut, status_code=201)
def post_draft(body: DraftCreate, db: Session = Depends(get_db), _: str = Depends(require_user)) -> DraftOut:
    if body.lead_id is not None and db.get(Lead, body.lead_id) is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Lead not found."})
    draft = EmailDraft(
        lead_id=body.lead_id,
        source_email_id=body.source_email_id,
        recipient=body.recipient.strip(),
        subject=body.subject,
        body=body.body,
        template_name=body.template_name,
        status="local_draft",
        approval_state="none",
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return DraftOut.model_validate(draft)


@router.patch("/drafts/{draft_id}", response_model=DraftOut)
def patch_draft(draft_id: int, body: DraftUpdate, db: Session = Depends(get_db), _: str = Depends(require_user)) -> DraftOut:
    draft = db.get(EmailDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Draft not found."})
    if draft.status == "provider_accepted":
        raise HTTPException(status_code=409, detail={"code": "already_sent", "message": "A sent message cannot be edited."})
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(draft, key, value)
    draft.updated_at = utcnow()
    db.commit()
    db.refresh(draft)
    return DraftOut.model_validate(draft)


@router.post("/drafts/{draft_id}/approve", response_model=DraftOut)
def approve_draft(draft_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)) -> DraftOut:
    draft = db.get(EmailDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Draft not found."})
    if draft.status == "provider_accepted":
        raise HTTPException(status_code=409, detail={"code": "already_sent", "message": "This message was already accepted by Gmail."})
    draft.approval_state = "approved"
    draft.approved_at = utcnow()
    if draft.status in {"local_draft", "pending_approval"}:
        draft.status = "approved"
    draft.updated_at = utcnow()
    write_audit(db, actor_type="user", action="draft.approve", entity_type="email_draft", entity_id=draft.id, result="ok", details={})
    db.commit()
    db.refresh(draft)
    return DraftOut.model_validate(draft)


@router.post("/drafts/{draft_id}/send")
def send(draft_id: int, body: SendRequest, db: Session = Depends(get_db), _: str = Depends(require_user)) -> dict:
    if not body.confirm:
        raise HTTPException(status_code=400, detail={"code": "confirmation_required", "message": "Set confirm=true to send this email."})
    try:
        result = send_draft(db, draft_id, user_confirmed=True, expected_recipient=body.recipient, actor="user")
    except MailFlowError as exc:
        db.commit()
        _mail_error(exc)
    db.commit()
    return result


@router.post("/drafts/{draft_id}/reconcile")
def reconcile(draft_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)) -> dict:
    draft = db.get(EmailDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Draft not found."})
    try:
        result = reconcile_send(db, draft)
    except GmailApiError as exc:
        _mail_error(exc)
    db.commit()
    return result


@router.post("/drafts/{draft_id}/gmail-draft")
def push_gmail_draft(draft_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)) -> dict:
    try:
        result = create_gmail_draft(db, draft_id)
    except (MailFlowError, GmailApiError) as exc:
        db.commit()
        _mail_error(exc)
    db.commit()
    return result


@router.post("/drafts/{draft_id}/queue-send")
def queue_send(draft_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)) -> dict:
    draft = db.get(EmailDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Draft not found."})
    if draft.approval_state != "approved":
        raise HTTPException(status_code=403, detail={"code": "approval_required", "message": "Approve the draft before queueing a send."})
    task, created = enqueue(
        db,
        task_type="send_email",
        payload={"draft_id": draft.id},
        idempotency_key=f"send_email:{draft.id}",
        priority=8,
    )
    db.commit()
    return {"task_id": task.id, "created": created, "status": task.status}
