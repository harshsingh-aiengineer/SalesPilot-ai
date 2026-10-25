"""Lead CRM endpoints."""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import require_user
from app.database import get_db
from app.models import AgentRun, EmailDraft, EmailMessage, IdempotencyRecord, Lead
from app.schemas import (
    ActivityOut,
    BulkLeadRequest,
    LeadCreate,
    LeadList,
    LeadOut,
    LeadUpdate,
    NoteCreate,
    NoteOut,
    OverviewOut,
    RunOut,
)
from app.services.leads import LeadError, add_note, bulk_update, create_lead, delete_lead, get_lead, list_activities, list_notes, search_leads, update_lead
from app.services.qualification import qualify_lead_record
from app.timeutils import business_day_bounds

router = APIRouter()


def _raise_lead(exc: LeadError) -> None:
    raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from exc


@router.get("/overview", response_model=OverviewOut)
def overview(db: Session = Depends(get_db), _: str = Depends(require_user)) -> OverviewOut:
    start, end = business_day_bounds()
    status_rows = db.query(Lead.status, func.count(Lead.id)).group_by(Lead.status).all()
    priority_rows = dict(db.query(Lead.priority, func.count(Lead.id)).group_by(Lead.priority).all())
    closed = ["won", "lost", "archived"]
    due_today = (
        db.query(func.count(Lead.id))
        .filter(Lead.next_follow_up_at >= start, Lead.next_follow_up_at < end, Lead.status.notin_(closed))
        .scalar()
        or 0
    )
    overdue = (
        db.query(func.count(Lead.id))
        .filter(Lead.next_follow_up_at.is_not(None), Lead.next_follow_up_at < start, Lead.status.notin_(closed))
        .scalar()
        or 0
    )
    email_rows = dict(db.query(EmailMessage.processing_status, func.count(EmailMessage.id)).group_by(EmailMessage.processing_status).all())
    runs = db.query(AgentRun).order_by(AgentRun.id.desc()).limit(6).all()
    pending = db.query(func.count(EmailDraft.id)).filter(EmailDraft.approval_state == "pending").scalar() or 0
    from app.models import AutomationTask

    failed = db.query(func.count(AutomationTask.id)).filter(AutomationTask.status == "failed").scalar() or 0
    return OverviewOut(
        leads_by_status={key: int(value) for key, value in status_rows},
        hot=int(priority_rows.get("hot", 0)),
        warm=int(priority_rows.get("warm", 0)),
        cold=int(priority_rows.get("cold", 0)),
        follow_ups_due_today=int(due_today),
        overdue_follow_ups=int(overdue),
        email_stats={key: int(value) for key, value in email_rows.items()},
        recent_runs=[RunOut.model_validate(run) for run in runs],
        pending_approvals=int(pending),
        failed_tasks=int(failed),
        total_leads=int(sum(value for _, value in status_rows)),
    )


@router.get("/leads", response_model=LeadList)
def list_leads(
    q: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    source: str | None = None,
    overdue: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
) -> LeadList:
    try:
        items, total = search_leads(db, q=q, status=status, priority=priority, source=source, overdue=overdue or None, limit=limit, offset=offset)
    except LeadError as exc:
        _raise_lead(exc)
    return LeadList(items=[LeadOut.model_validate(item) for item in items], total=total)


@router.post("/leads", response_model=LeadOut, status_code=201)
def post_lead(
    body: LeadCreate,
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> LeadOut:
    if idempotency_key:
        digest = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True, default=str).encode()).hexdigest()
        existing = db.query(IdempotencyRecord).filter(IdempotencyRecord.key == idempotency_key).one_or_none()
        if existing is not None:
            if existing.request_hash != digest:
                raise HTTPException(status_code=409, detail={"code": "idempotency_conflict", "message": "This idempotency key was already used with a different request."})
            stored = json.loads(existing.response_json)
            return LeadOut.model_validate(stored)
    lead = create_lead(db, body, actor="user")
    db.commit()
    db.refresh(lead)
    payload = LeadOut.model_validate(lead)
    if idempotency_key:
        db.add(
            IdempotencyRecord(
                key=idempotency_key,
                request_hash=digest,
                status_code=201,
                response_json=payload.model_dump_json(),
            )
        )
        db.commit()
    return payload


@router.get("/leads/{lead_id}", response_model=LeadOut)
def read_lead(lead_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)) -> LeadOut:
    try:
        lead = get_lead(db, lead_id)
    except LeadError as exc:
        _raise_lead(exc)
    return LeadOut.model_validate(lead)


@router.patch("/leads/{lead_id}", response_model=LeadOut)
def patch_lead(lead_id: int, body: LeadUpdate, db: Session = Depends(get_db), _: str = Depends(require_user)) -> LeadOut:
    try:
        lead = update_lead(db, lead_id, body, actor="user")
    except LeadError as exc:
        _raise_lead(exc)
    db.commit()
    db.refresh(lead)
    return LeadOut.model_validate(lead)


@router.delete("/leads/{lead_id}")
def remove_lead(lead_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)) -> dict:
    try:
        delete_lead(db, lead_id, actor="user")
    except LeadError as exc:
        _raise_lead(exc)
    db.commit()
    return {"ok": True, "deleted": lead_id}


@router.post("/leads/bulk")
def bulk_leads(body: BulkLeadRequest, db: Session = Depends(get_db), _: str = Depends(require_user)) -> dict:
    try:
        result = bulk_update(
            db,
            action=body.action,
            ids=body.ids,
            confirm=body.confirm,
            status=body.status,
            next_follow_up_at=body.next_follow_up_at,
        )
    except LeadError as exc:
        _raise_lead(exc)
    db.commit()
    return result


@router.get("/leads/{lead_id}/notes", response_model=list[NoteOut])
def get_notes(lead_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)) -> list[NoteOut]:
    try:
        notes = list_notes(db, lead_id)
    except LeadError as exc:
        _raise_lead(exc)
    return [NoteOut.model_validate(note) for note in notes]


@router.post("/leads/{lead_id}/notes", response_model=NoteOut, status_code=201)
def post_note(lead_id: int, body: NoteCreate, db: Session = Depends(get_db), _: str = Depends(require_user)) -> NoteOut:
    try:
        note = add_note(db, lead_id, body.body, author_type="user")
    except LeadError as exc:
        _raise_lead(exc)
    db.commit()
    db.refresh(note)
    return NoteOut.model_validate(note)


@router.get("/leads/{lead_id}/activity", response_model=list[ActivityOut])
def get_activity(lead_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)) -> list[ActivityOut]:
    try:
        rows = list_activities(db, lead_id)
    except LeadError as exc:
        _raise_lead(exc)
    return [ActivityOut.model_validate(row) for row in rows]


@router.get("/leads/{lead_id}/emails")
def lead_emails(lead_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)) -> dict:
    try:
        get_lead(db, lead_id)
    except LeadError as exc:
        _raise_lead(exc)
    rows = db.query(EmailMessage).filter(EmailMessage.lead_id == lead_id).order_by(EmailMessage.id.desc()).all()
    from app.schemas import EmailOut

    return {"items": [EmailOut.model_validate(row).model_dump(mode="json") for row in rows]}


@router.post("/leads/{lead_id}/qualify")
def qualify(lead_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)) -> dict:
    from app.llm import LlmError
    from app.services.pipeline import _llm_json

    try:
        get_lead(db, lead_id)
    except LeadError as exc:
        _raise_lead(exc)
    llm_error = None
    payload = None
    lead = get_lead(db, lead_id)
    try:
        payload = _llm_json(
            db,
            "Return JSON only with score, priority, explanation, missing_fields, recommended_action, confidence, "
            f"requirements, location, budget_min, budget_max, deadline. Do not invent facts.\n{lead.name}\n{lead.requirements}\n{lead.notes}",
            holder=f"api-qualify-{lead_id}",
        )
    except (LlmError, json.JSONDecodeError, TypeError, ValueError) as exc:
        llm_error = getattr(exc, "message", None) or exc.__class__.__name__
    result = qualify_lead_record(db, lead_id, llm_payload=payload if isinstance(payload, dict) else None, actor="user")
    db.commit()
    body = result.model_dump()
    if llm_error:
        body["llm_error"] = llm_error
    return body
