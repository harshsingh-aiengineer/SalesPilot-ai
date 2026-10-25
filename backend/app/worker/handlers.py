"""Task handlers. Each handler must be safe to run more than once."""

from __future__ import annotations

import json
from datetime import timedelta, timezone

from sqlalchemy.orm import Session

from app.gmail.client import GmailApiError
from app.gmail.sync import sync_inbox
from app.models import AutomationTask, Lead, SchedulerJob
from app.services.pipeline import MailFlowError, maybe_create_draft, process_stored_email, send_draft
from app.services.settings_store import get_settings_row
from app.services.tasks import enqueue
from app.timeutils import business_tz, utcnow, within_business_hours


class NonRetryable(Exception):
    pass


def _job(db: Session, name: str) -> SchedulerJob:
    job = db.get(SchedulerJob, name)
    if job is None:
        job = SchedulerJob(name=name)
        db.add(job)
        db.flush()
    return job


def schedule_periodic(db: Session) -> None:
    settings = get_settings_row(db)
    now = utcnow()
    if settings.inbox_poll_enabled:
        job = _job(db, "gmail_poll")
        if job.next_run_at is None or job.next_run_at <= now:
            from app.gmail.oauth import client_secrets_status, token_file_present

            gmail_ready = client_secrets_status()["configured"] and token_file_present()
            active = None
            if gmail_ready:
                active = (
                    db.query(AutomationTask)
                    .filter(
                        AutomationTask.task_type == "gmail_poll",
                        AutomationTask.status.in_(["queued", "running", "retrying"]),
                    )
                    .first()
                )
            if gmail_ready and active is None:
                enqueue(
                    db,
                    task_type="gmail_poll",
                    payload={"reason": "scheduled"},
                    idempotency_key=f"gmail_poll:{now.strftime('%Y%m%d%H%M%S')}",
                    priority=1,
                )
            job.last_run_at = now
            job.next_run_at = now + timedelta(seconds=settings.inbox_poll_interval_seconds)
    if settings.follow_up_scan_enabled:
        job = _job(db, "follow_up_scan")
        if job.next_run_at is None or job.next_run_at <= now:
            active = (
                db.query(AutomationTask)
                .filter(
                    AutomationTask.task_type == "follow_up_scan",
                    AutomationTask.status.in_(["queued", "running", "retrying"]),
                )
                .first()
            )
            if active is None:
                enqueue(
                    db,
                    task_type="follow_up_scan",
                    payload={},
                    idempotency_key=f"follow_up_scan:{now.strftime('%Y%m%d%H%M%S')}",
                    priority=2,
                )
            job.last_run_at = now
            job.next_run_at = now + timedelta(minutes=15)


def next_business_open(db: Session):
    settings = get_settings_row(db)
    allowed = {part.strip().lower()[:3] for part in settings.business_days.split(",") if part.strip()}
    tz = business_tz()
    local_now = utcnow().replace(tzinfo=timezone.utc).astimezone(tz)
    for offset in range(0, 8):
        day = local_now + timedelta(days=offset)
        candidate = day.replace(hour=settings.business_hours_start, minute=5, second=0, microsecond=0)
        weekday = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][candidate.weekday()]
        if weekday not in allowed:
            continue
        if candidate <= local_now:
            continue
        return candidate.astimezone(timezone.utc).replace(tzinfo=None)
    return utcnow() + timedelta(hours=12)


def handle_gmail_poll(db: Session, task: AutomationTask) -> dict:
    try:
        return sync_inbox(db, max_results=15)
    except GmailApiError as exc:
        if exc.code in {"not_authorized", "missing_client_secret", "gmail_unauthorized", "credentials_invalid", "refresh_failed"}:
            raise NonRetryable(exc.message) from exc
        raise


def handle_process_email(db: Session, task: AutomationTask) -> dict:
    payload = json.loads(task.payload_json or "{}")
    email_id = payload.get("email_message_id")
    if not email_id:
        raise NonRetryable("process_email task is missing email_message_id.")
    return process_stored_email(db, int(email_id), use_llm=True)


def handle_follow_up_scan(db: Session, task: AutomationTask) -> dict:
    now = utcnow()
    leads = (
        db.query(Lead)
        .filter(
            Lead.next_follow_up_at.is_not(None),
            Lead.next_follow_up_at <= now,
            Lead.status.notin_(["won", "lost", "archived"]),
        )
        .all()
    )
    created = 0
    for lead in leads:
        when = lead.next_follow_up_at or now
        _, was_created = enqueue(
            db,
            task_type="follow_up_lead",
            payload={"lead_id": lead.id},
            idempotency_key=f"follow_up_lead:{lead.id}:{when.strftime('%Y%m%d%H%M')}",
            priority=4,
        )
        if was_created:
            created += 1
    return {"examined": len(leads), "enqueued": created}


def handle_follow_up_lead(db: Session, task: AutomationTask) -> dict:
    settings = get_settings_row(db)
    if not within_business_hours(start_hour=settings.business_hours_start, end_hour=settings.business_hours_end, days=settings.business_days):
        task.attempts = max(0, task.attempts - 1)
        task.status = "queued"
        task.locked_at = None
        task.scheduled_at = next_business_open(db)
        task.updated_at = utcnow()
        task.last_error = "Deferred until business hours."
        return {"deferred": True, "scheduled_at": task.scheduled_at.isoformat()}
    payload = json.loads(task.payload_json or "{}")
    lead = db.get(Lead, int(payload.get("lead_id") or 0))
    if lead is None:
        raise NonRetryable("Lead no longer exists.")
    if lead.next_follow_up_at and lead.next_follow_up_at > utcnow() + timedelta(minutes=1):
        return {"skipped": True, "reason": "follow_up_not_due", "lead_id": lead.id}
    missing = []
    if lead.missing_information:
        try:
            parsed = json.loads(lead.missing_information)
            if isinstance(parsed, list):
                missing = [str(item) for item in parsed]
        except json.JSONDecodeError:
            missing = [lead.missing_information]
    draft = maybe_create_draft(db, lead, missing or ["measurements", "budget"])
    return {
        "lead_id": lead.id,
        "draft_id": draft.id if draft else None,
        "skipped": draft is None,
        "reason": None if draft else "cooldown_or_drafts_disabled",
    }


def handle_send_email(db: Session, task: AutomationTask) -> dict:
    payload = json.loads(task.payload_json or "{}")
    draft_id = payload.get("draft_id")
    if not draft_id:
        raise NonRetryable("send_email task is missing draft_id.")
    try:
        return send_draft(db, int(draft_id), user_confirmed=False, actor="worker")
    except MailFlowError as exc:
        if exc.code in {"approval_required", "send_blocked", "not_found", "recipient_mismatch", "empty_draft", "invalid_recipient"}:
            raise NonRetryable(exc.message) from exc
        if exc.code == "send_unknown":
            raise NonRetryable(exc.message) from exc
        raise


HANDLERS = {
    "gmail_poll": handle_gmail_poll,
    "process_email": handle_process_email,
    "follow_up_scan": handle_follow_up_scan,
    "follow_up_lead": handle_follow_up_lead,
    "send_email": handle_send_email,
}
