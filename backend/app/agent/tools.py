"""Allowlisted tools. The model cannot call anything that is not registered here."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.gmail.client import GmailApiError, GmailClient
from app.gmail.sync import sync_inbox
from app.models import AutomationTask, EmailDraft, EmailMessage, Lead
from app.schemas import DraftCreate, LeadCreate, LeadUpdate
from app.services.leads import (
    LeadError,
    add_note,
    create_lead,
    get_lead,
    search_leads,
    update_lead,
)
from app.services.pipeline import MailFlowError, extract_email, process_stored_email, send_draft
from app.services.qualification import qualify_lead_record
from app.services.settings_store import get_settings_row
from app.services.tasks import TaskError, enqueue, list_tasks, retry_task
from app.timeutils import business_day_bounds, utcnow

ToolHandler = Callable[[Session, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    permission: str
    timeout_seconds: float
    handler: ToolHandler

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _lead_brief(lead: Lead) -> dict[str, Any]:
    return {
        "id": lead.id,
        "name": lead.name,
        "email": lead.email,
        "phone": lead.phone,
        "company": lead.company,
        "location": lead.location,
        "requirements": (lead.requirements or "")[:500],
        "budget_min": lead.budget_min,
        "budget_max": lead.budget_max,
        "currency": lead.currency,
        "source": lead.source,
        "status": lead.status,
        "priority": lead.priority,
        "qualification_score": lead.qualification_score,
        "missing_information": lead.missing_information,
        "next_action": lead.next_action,
        "next_follow_up_at": lead.next_follow_up_at.isoformat() if lead.next_follow_up_at else None,
        "is_synthetic": lead.is_synthetic,
    }


def _parse_when(value: str) -> datetime:
    from datetime import timezone

    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def tool_search_leads(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    items, total = search_leads(
        db,
        q=args.get("query"),
        status=args.get("status"),
        priority=args.get("priority"),
        overdue=args.get("overdue"),
        limit=min(int(args.get("limit") or 10), 25),
    )
    return {"ok": True, "total": total, "leads": [_lead_brief(lead) for lead in items]}


def tool_get_lead(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    lead = get_lead(db, int(args["lead_id"]))
    payload = _lead_brief(lead)
    payload["notes"] = (lead.notes or "")[:1000]
    payload["qualification_summary"] = lead.qualification_summary
    return {"ok": True, "lead": payload}


def tool_create_lead(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    data = LeadCreate.model_validate(args)
    lead = create_lead(db, data, actor="agent")
    return {"ok": True, "lead": _lead_brief(lead)}


def tool_update_lead(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    lead_id = int(args["lead_id"])
    fields = {key: value for key, value in args.items() if key != "lead_id"}
    data = LeadUpdate.model_validate(fields)
    lead = update_lead(db, lead_id, data, actor="agent")
    return {"ok": True, "lead": _lead_brief(lead)}


def tool_add_note(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    note = add_note(db, int(args["lead_id"]), str(args["note"]), author_type="agent")
    return {"ok": True, "note_id": note.id, "lead_id": note.lead_id}


def tool_qualify(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    from app.llm import LlmError

    lead = get_lead(db, int(args["lead_id"]))
    llm_error = None
    payload = None
    try:
        from app.services.pipeline import _llm_json

        prompt = (
            "Qualify this sales lead. Return JSON with score (0-100), priority (hot|warm|cold), "
            "explanation, missing_fields, recommended_action, confidence, requirements, location, "
            "budget_min, budget_max, deadline. Use null when unknown. Do not invent facts.\n"
            f"Name: {lead.name}\nLocation: {lead.location}\nRequirements: {lead.requirements}\n"
            f"Notes: {(lead.notes or '')[:1500]}\nBudget: {lead.budget_min}-{lead.budget_max} {lead.currency}"
        )
        payload = _llm_json(db, prompt, holder=f"qualify-{lead.id}")
    except (LlmError, json.JSONDecodeError, TypeError, ValueError, MailFlowError) as exc:
        llm_error = getattr(exc, "message", None) or exc.__class__.__name__
        payload = None
    if payload and isinstance(payload, dict):
        result = qualify_lead_record(db, lead.id, llm_payload=payload, actor="agent")
    else:
        result = qualify_lead_record(db, lead.id, llm_payload=None, actor="agent")
        if llm_error:
            # qualify_lead_record persisted rules. Surface the model failure.
            result_dump = result.model_dump()
            result_dump["llm_error"] = llm_error
            return {"ok": True, "qualification": result_dump, "llm_error": llm_error}
    return {"ok": True, "qualification": result.model_dump()}


def tool_schedule_follow_up(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    when = _parse_when(str(args["when"]))
    lead = update_lead(
        db,
        int(args["lead_id"]),
        LeadUpdate(next_follow_up_at=when, next_action=args.get("note")),
        actor="agent",
    )
    return {"ok": True, "lead_id": lead.id, "next_follow_up_at": when.isoformat()}


def tool_overdue(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    items, total = search_leads(db, overdue=True, limit=min(int(args.get("limit") or 10), 25))
    return {"ok": True, "total": total, "leads": [_lead_brief(lead) for lead in items]}


def tool_sales_summary(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    start, end = business_day_bounds()
    by_status = dict(db.query(Lead.status, func.count(Lead.id)).group_by(Lead.status).all())
    by_priority = dict(db.query(Lead.priority, func.count(Lead.id)).group_by(Lead.priority).all())
    overdue = (
        db.query(func.count(Lead.id))
        .filter(
            Lead.next_follow_up_at.is_not(None),
            Lead.next_follow_up_at < start,
            Lead.status.notin_(["won", "lost", "archived"]),
        )
        .scalar()
        or 0
    )
    due_today = (
        db.query(func.count(Lead.id))
        .filter(Lead.next_follow_up_at >= start, Lead.next_follow_up_at < end, Lead.status.notin_(["won", "lost", "archived"]))
        .scalar()
        or 0
    )
    quotation = by_status.get("quotation", 0)
    return {
        "ok": True,
        "leads_by_status": by_status,
        "leads_by_priority": by_priority,
        "overdue_follow_ups": int(overdue),
        "follow_ups_due_today": int(due_today),
        "waiting_for_quotation": int(quotation),
        "total_leads": int(sum(by_status.values())),
    }


def tool_list_emails(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    limit = min(int(args.get("limit") or 10), 25)
    rows = db.query(EmailMessage).order_by(EmailMessage.received_at.desc().nulls_last(), EmailMessage.id.desc()).limit(limit).all()
    return {
        "ok": True,
        "emails": [
            {
                "id": row.id,
                "sender": row.sender,
                "subject": row.subject,
                "snippet": row.snippet,
                "processing_status": row.processing_status,
                "lead_id": row.lead_id,
                "received_at": row.received_at.isoformat() if row.received_at else None,
                "is_synthetic": row.is_synthetic,
            }
            for row in rows
        ],
    }


def tool_email_details(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    row = db.get(EmailMessage, int(args["email_id"]))
    if row is None:
        return _error("not_found", "Email not found")
    return {
        "ok": True,
        "email": {
            "id": row.id,
            "sender": row.sender,
            "recipients": row.recipients,
            "subject": row.subject,
            "body_text": (row.body_text or "")[:4000],
            "processing_status": row.processing_status,
            "classification": row.classification,
            "lead_id": row.lead_id,
            "provider_message_id": row.provider_message_id,
            "is_synthetic": row.is_synthetic,
        },
    }


def tool_search_emails(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    source = (args.get("source") or "local").lower()
    limit = min(int(args.get("limit") or 10), 20)
    if source == "gmail":
        try:
            result = sync_inbox(db, max_results=limit, client=GmailClient())
        except GmailApiError as exc:
            return _error(exc.code, exc.message)
        return {"ok": True, "source": "gmail", "sync": result}
    query = (args.get("query") or "").strip()
    rows = db.query(EmailMessage)
    if query:
        like = f"%{query}%"
        rows = rows.filter(
            (EmailMessage.subject.ilike(like)) | (EmailMessage.sender.ilike(like)) | (EmailMessage.body_text.ilike(like))
        )
    found = rows.order_by(EmailMessage.id.desc()).limit(limit).all()
    return {
        "ok": True,
        "source": "local",
        "emails": [{"id": row.id, "subject": row.subject, "sender": row.sender, "lead_id": row.lead_id} for row in found],
    }


def tool_extract(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    email = db.get(EmailMessage, int(args["email_id"]))
    if email is None:
        return _error("not_found", "Email not found")
    extraction = extract_email(db, email, use_llm=True)
    email.extraction_json = json.dumps(extraction, default=str)[:8000]
    return {"ok": True, "email_id": email.id, "extraction": extraction}


def tool_create_draft(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    data = DraftCreate.model_validate(
        {
            "lead_id": args.get("lead_id"),
            "recipient": args["recipient"],
            "subject": args.get("subject") or "",
            "body": args.get("body") or "",
            "template_name": "agent",
        }
    )
    if data.lead_id is not None:
        get_lead(db, data.lead_id)
    draft = EmailDraft(
        lead_id=data.lead_id,
        recipient=data.recipient,
        subject=data.subject,
        body=data.body,
        template_name="agent",
        status="pending_approval",
        approval_state="pending",
    )
    db.add(draft)
    db.flush()
    return {"ok": True, "draft_id": draft.id, "status": draft.status, "approval_state": draft.approval_state}


def tool_send(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    draft = db.get(EmailDraft, int(args["draft_id"]))
    if draft is None:
        return _error("not_found", "Draft not found")
    if draft.approval_state != "approved":
        return _error("approval_required", "The user has not approved this draft. It was not sent.")
    try:
        result = send_draft(db, draft.id, user_confirmed=False, actor="agent")
    except MailFlowError as exc:
        return _error(exc.code, exc.message)
    return result


def tool_list_tasks(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    status = args.get("status")
    items, total = list_tasks(db, status=status, limit=min(int(args.get("limit") or 10), 25))
    return {
        "ok": True,
        "total": total,
        "tasks": [
            {
                "id": task.id,
                "task_type": task.task_type,
                "status": task.status,
                "attempts": task.attempts,
                "max_attempts": task.max_attempts,
                "last_error": task.last_error,
                "scheduled_at": task.scheduled_at.isoformat() if task.scheduled_at else None,
            }
            for task in items
        ],
    }


def tool_create_follow_up_task(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    lead = get_lead(db, int(args["lead_id"]))
    when = _parse_when(str(args["when"]))
    lead.next_follow_up_at = when
    lead.updated_at = utcnow()
    task, created = enqueue(
        db,
        task_type="follow_up_lead",
        payload={"lead_id": lead.id, "note": args.get("note")},
        idempotency_key=f"follow_up_task:{lead.id}:{when.strftime('%Y%m%d%H%M')}",
        scheduled_at=when,
        priority=3,
    )
    return {"ok": True, "task_id": task.id, "created": created, "status": task.status, "scheduled_at": when.isoformat()}


def tool_task_status(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    task = db.get(AutomationTask, int(args["task_id"]))
    if task is None:
        return _error("not_found", "Task not found")
    return {
        "ok": True,
        "task": {
            "id": task.id,
            "task_type": task.task_type,
            "status": task.status,
            "attempts": task.attempts,
            "last_error": task.last_error,
            "result_json": task.result_json,
        },
    }


def tool_daily_summary(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    summary = tool_sales_summary(db, {})
    failed, failed_total = list_tasks(db, status="failed", limit=5)
    settings = get_settings_row(db)
    text = (
        f"Pipeline has {summary['total_leads']} leads. "
        f"Hot {summary['leads_by_priority'].get('hot', 0)}, "
        f"warm {summary['leads_by_priority'].get('warm', 0)}, "
        f"cold {summary['leads_by_priority'].get('cold', 0)}. "
        f"Follow-ups due today: {summary['follow_ups_due_today']}. "
        f"Overdue: {summary['overdue_follow_ups']}. "
        f"Waiting for quotation: {summary['waiting_for_quotation']}. "
        f"Failed tasks: {failed_total}. "
        f"Inbox query: {settings.gmail_search_query}."
    )
    return {"ok": True, "summary": text, "details": summary, "failed_task_ids": [task.id for task in failed]}


def tool_retry(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    try:
        task = retry_task(db, int(args["task_id"]), actor="agent")
    except TaskError as exc:
        return _error(exc.code, exc.message)
    return {"ok": True, "task_id": task.id, "status": task.status, "max_attempts": task.max_attempts}


def tool_process_email(db: Session, args: dict[str, Any]) -> dict[str, Any]:
    try:
        return process_stored_email(db, int(args["email_id"]), use_llm=True)
    except MailFlowError as exc:
        return _error(exc.code, exc.message)


_OBJECT = {"type": "object", "properties": {}, "additionalProperties": False}

TOOLS: list[ToolSpec] = [
    ToolSpec("search_leads", "Search CRM leads by text, status, or priority.", {"type": "object", "properties": {"query": {"type": "string"}, "status": {"type": "string"}, "priority": {"type": "string"}, "overdue": {"type": "boolean"}, "limit": {"type": "integer"}}, "additionalProperties": False}, "read", 10, tool_search_leads),
    ToolSpec("get_lead", "Get one CRM lead by id.", {"type": "object", "properties": {"lead_id": {"type": "integer"}}, "required": ["lead_id"], "additionalProperties": False}, "read", 10, tool_get_lead),
    ToolSpec("create_lead", "Create a CRM lead from known facts.", {"type": "object", "properties": {"name": {"type": "string"}, "email": {"type": "string"}, "phone": {"type": "string"}, "company": {"type": "string"}, "location": {"type": "string"}, "requirements": {"type": "string"}, "budget_min": {"type": "number"}, "budget_max": {"type": "number"}, "currency": {"type": "string"}, "source": {"type": "string"}, "status": {"type": "string"}, "priority": {"type": "string"}, "notes": {"type": "string"}}, "required": ["name"], "additionalProperties": False}, "write", 10, tool_create_lead),
    ToolSpec("update_lead", "Update fields on an existing lead.", {"type": "object", "properties": {"lead_id": {"type": "integer"}, "name": {"type": "string"}, "email": {"type": "string"}, "phone": {"type": "string"}, "company": {"type": "string"}, "location": {"type": "string"}, "requirements": {"type": "string"}, "status": {"type": "string"}, "priority": {"type": "string"}, "notes": {"type": "string"}, "next_action": {"type": "string"}}, "required": ["lead_id"], "additionalProperties": False}, "write", 10, tool_update_lead),
    ToolSpec("add_lead_note", "Append a note to a lead timeline.", {"type": "object", "properties": {"lead_id": {"type": "integer"}, "note": {"type": "string"}}, "required": ["lead_id", "note"], "additionalProperties": False}, "write", 10, tool_add_note),
    ToolSpec("qualify_lead", "Score a lead and save priority, missing information, and next action.", {"type": "object", "properties": {"lead_id": {"type": "integer"}}, "required": ["lead_id"], "additionalProperties": False}, "write", 150, tool_qualify),
    ToolSpec("schedule_follow_up", "Set the next follow-up datetime on a lead.", {"type": "object", "properties": {"lead_id": {"type": "integer"}, "when": {"type": "string"}, "note": {"type": "string"}}, "required": ["lead_id", "when"], "additionalProperties": False}, "write", 10, tool_schedule_follow_up),
    ToolSpec("list_overdue_follow_ups", "List leads whose follow-up date is before today.", {"type": "object", "properties": {"limit": {"type": "integer"}}, "additionalProperties": False}, "read", 10, tool_overdue),
    ToolSpec("get_sales_summary", "Count leads by status and priority from the database.", _OBJECT, "read", 10, tool_sales_summary),
    ToolSpec("list_recent_emails", "List emails already stored from Gmail sync.", {"type": "object", "properties": {"limit": {"type": "integer"}}, "additionalProperties": False}, "read", 10, tool_list_emails),
    ToolSpec("get_email_details", "Read one stored email.", {"type": "object", "properties": {"email_id": {"type": "integer"}}, "required": ["email_id"], "additionalProperties": False}, "read", 10, tool_email_details),
    ToolSpec("search_emails", "Search stored email, or pass source=gmail to sync the configured Gmail query.", {"type": "object", "properties": {"query": {"type": "string"}, "source": {"type": "string"}, "limit": {"type": "integer"}}, "additionalProperties": False}, "read", 60, tool_search_emails),
    ToolSpec("extract_lead_from_email", "Extract lead facts from a stored email without inventing missing data.", {"type": "object", "properties": {"email_id": {"type": "integer"}}, "required": ["email_id"], "additionalProperties": False}, "read", 150, tool_extract),
    ToolSpec("create_email_draft", "Save a local email draft that still requires user approval before sending.", {"type": "object", "properties": {"lead_id": {"type": "integer"}, "recipient": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["recipient", "subject", "body"], "additionalProperties": False}, "write", 10, tool_create_draft),
    ToolSpec("send_approved_email", "Send a draft only when the user has already approved it.", {"type": "object", "properties": {"draft_id": {"type": "integer"}}, "required": ["draft_id"], "additionalProperties": False}, "external", 60, tool_send),
    ToolSpec("list_pending_tasks", "List automation tasks. Pass status=failed to see failures.", {"type": "object", "properties": {"status": {"type": "string"}, "limit": {"type": "integer"}}, "additionalProperties": False}, "read", 10, tool_list_tasks),
    ToolSpec("create_follow_up_task", "Schedule a follow-up task for a lead.", {"type": "object", "properties": {"lead_id": {"type": "integer"}, "when": {"type": "string"}, "note": {"type": "string"}}, "required": ["lead_id", "when"], "additionalProperties": False}, "write", 10, tool_create_follow_up_task),
    ToolSpec("get_task_status", "Read one automation task.", {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"], "additionalProperties": False}, "read", 10, tool_task_status),
    ToolSpec("generate_daily_sales_summary", "Build today's pipeline summary from database counts.", _OBJECT, "read", 10, tool_daily_summary),
    ToolSpec("retry_failed_task", "Queue one more attempt for a failed or cancelled task.", {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"], "additionalProperties": False}, "write", 10, tool_retry),
]

REGISTRY = {tool.name: tool for tool in TOOLS}


def tool_schemas() -> list[dict[str, Any]]:
    return [tool.schema() for tool in TOOLS]


def execute_tool(db: Session, name: str, arguments: Any) -> dict[str, Any]:
    spec = REGISTRY.get(name)
    if spec is None:
        return _error("unregistered_tool", f"{name} is not an allowlisted tool.")
    if not isinstance(arguments, dict):
        return _error("invalid_arguments", "Tool arguments must be an object.")
    try:
        if spec.name == "qualify_lead":
            return spec.handler(db, arguments)
        return spec.handler(db, arguments)
    except (LeadError, MailFlowError, TaskError) as exc:
        return _error(exc.code, exc.message)
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        return _error("invalid_arguments", str(exc)[:500])
    except Exception as exc:  # noqa: BLE001
        return _error("tool_failed", f"{spec.name} failed: {exc.__class__.__name__}")
