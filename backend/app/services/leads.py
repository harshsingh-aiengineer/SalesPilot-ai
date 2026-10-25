"""CRM lead persistence."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import EmailDraft, EmailMessage, Lead, LeadActivity, LeadNote
from app.schemas import LEAD_PRIORITIES, LEAD_STATUSES, LeadCreate, LeadUpdate
from app.services.audit import write_audit
from app.timeutils import business_day_bounds, utcnow


class LeadError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def _activity(db: Session, lead_id: int, activity_type: str, summary: str, details: dict | None = None) -> None:
    db.add(
        LeadActivity(
            lead_id=lead_id,
            activity_type=activity_type,
            summary=summary[:500],
            details_json=json.dumps(details or {}, default=str),
        )
    )


def create_lead(db: Session, data: LeadCreate, actor: str = "user") -> Lead:
    payload = data.model_dump()
    lead = Lead(**payload, created_at=utcnow(), updated_at=utcnow())
    db.add(lead)
    db.flush()
    _activity(db, lead.id, "created", f"Lead created ({lead.source or 'manual'})")
    write_audit(
        db,
        actor_type=actor,
        action="lead.create",
        entity_type="lead",
        entity_id=lead.id,
        result="ok",
        details={"name": lead.name, "is_synthetic": lead.is_synthetic},
    )
    return lead


def update_lead(db: Session, lead_id: int, data: LeadUpdate, actor: str = "user") -> Lead:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise LeadError("not_found", "Lead not found", 404)
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return lead
    for key, value in changes.items():
        setattr(lead, key, value)
    lead.updated_at = utcnow()
    _activity(db, lead.id, "updated", "Lead fields updated", {"fields": sorted(changes.keys())})
    write_audit(
        db,
        actor_type=actor,
        action="lead.update",
        entity_type="lead",
        entity_id=lead.id,
        result="ok",
        details={"fields": sorted(changes.keys())},
    )
    return lead


def delete_lead(db: Session, lead_id: int, actor: str = "user") -> None:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise LeadError("not_found", "Lead not found", 404)
    db.query(EmailMessage).filter(EmailMessage.lead_id == lead_id).update({"lead_id": None})
    db.query(EmailDraft).filter(EmailDraft.lead_id == lead_id).update({"lead_id": None})
    write_audit(
        db,
        actor_type=actor,
        action="lead.delete",
        entity_type="lead",
        entity_id=lead_id,
        result="ok",
        details={"name": lead.name},
    )
    db.delete(lead)


def get_lead(db: Session, lead_id: int) -> Lead:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise LeadError("not_found", "Lead not found", 404)
    return lead


def search_leads(
    db: Session,
    *,
    q: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    source: str | None = None,
    overdue: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Lead], int]:
    query = db.query(Lead)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Lead.name.ilike(like),
                Lead.email.ilike(like),
                Lead.company.ilike(like),
                Lead.location.ilike(like),
                Lead.requirements.ilike(like),
                Lead.phone.ilike(like),
            )
        )
    if status:
        if status not in LEAD_STATUSES:
            raise LeadError("invalid_filter", "Unknown lead status")
        query = query.filter(Lead.status == status)
    if priority:
        if priority not in LEAD_PRIORITIES:
            raise LeadError("invalid_filter", "Unknown lead priority")
        query = query.filter(Lead.priority == priority)
    if source:
        query = query.filter(Lead.source == source)
    if overdue:
        start, _end = business_day_bounds()
        query = query.filter(
            Lead.next_follow_up_at.is_not(None),
            Lead.next_follow_up_at < start,
            Lead.status.notin_(["won", "lost", "archived"]),
        )
    total = query.with_entities(func.count(Lead.id)).scalar() or 0
    items = query.order_by(Lead.updated_at.desc()).offset(offset).limit(limit).all()
    return items, int(total)


def add_note(db: Session, lead_id: int, body: str, author_type: str = "user") -> LeadNote:
    get_lead(db, lead_id)
    note = LeadNote(lead_id=lead_id, body=body.strip(), author_type=author_type)
    db.add(note)
    _activity(db, lead_id, "note", body.strip()[:180])
    db.flush()
    return note


def list_notes(db: Session, lead_id: int) -> list[LeadNote]:
    get_lead(db, lead_id)
    return db.query(LeadNote).filter(LeadNote.lead_id == lead_id).order_by(LeadNote.created_at.desc()).all()


def list_activities(db: Session, lead_id: int) -> list[LeadActivity]:
    get_lead(db, lead_id)
    return (
        db.query(LeadActivity)
        .filter(LeadActivity.lead_id == lead_id)
        .order_by(LeadActivity.created_at.desc())
        .all()
    )


def find_lead_by_email(db: Session, email: str | None) -> Lead | None:
    if not email:
        return None
    return db.query(Lead).filter(func.lower(Lead.email) == email.strip().lower()).order_by(Lead.id.asc()).first()


def bulk_update(
    db: Session,
    *,
    action: str,
    ids: list[int],
    confirm: bool,
    status: str | None,
    next_follow_up_at: datetime | None,
    actor: str = "user",
) -> dict:
    unique_ids = list(dict.fromkeys(ids))
    found = db.query(Lead).filter(Lead.id.in_(unique_ids)).all()
    if len(found) != len(unique_ids):
        missing = sorted(set(unique_ids) - {lead.id for lead in found})
        raise LeadError("not_found", f"Unknown lead ids: {missing}", 404)
    if action == "delete":
        if not confirm:
            raise LeadError("confirmation_required", "Bulk delete requires confirm=true", 400)
        for lead in found:
            delete_lead(db, lead.id, actor=actor)
        return {"affected": len(found), "action": action}
    if action == "set_status":
        if status not in LEAD_STATUSES:
            raise LeadError("invalid_status", "A valid status is required")
        for lead in found:
            lead.status = status
            lead.updated_at = utcnow()
            _activity(db, lead.id, "bulk_status", f"Status set to {status}")
        write_audit(
            db,
            actor_type=actor,
            action="lead.bulk_status",
            entity_type="lead",
            entity_id=",".join(str(i) for i in unique_ids),
            result="ok",
            details={"status": status, "count": len(found)},
        )
        return {"affected": len(found), "action": action}
    if action == "set_follow_up":
        if next_follow_up_at is None:
            raise LeadError("invalid_follow_up", "next_follow_up_at is required")
        for lead in found:
            lead.next_follow_up_at = next_follow_up_at
            lead.updated_at = utcnow()
            _activity(db, lead.id, "follow_up_scheduled", f"Follow-up set to {next_follow_up_at.isoformat()}")
        return {"affected": len(found), "action": action}
    raise LeadError("invalid_action", "Unsupported bulk action")
