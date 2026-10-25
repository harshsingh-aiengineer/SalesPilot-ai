"""Persistent task queue helpers. The worker owns execution."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import AutomationTask, TaskLog
from app.services.audit import write_audit
from app.services.settings_store import get_settings_row
from app.timeutils import utcnow


ACTIVE_QUEUE = ("queued", "retrying")
TERMINAL = ("succeeded", "failed", "cancelled")


class TaskError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def enqueue(
    db: Session,
    *,
    task_type: str,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    priority: int = 0,
    scheduled_at: datetime | None = None,
    max_attempts: int | None = None,
) -> tuple[AutomationTask, bool]:
    """Insert a task. Returns (task, created). Duplicate idempotency keys return the existing row."""
    if idempotency_key:
        existing = db.query(AutomationTask).filter(AutomationTask.idempotency_key == idempotency_key).one_or_none()
        if existing is not None:
            return existing, False
    settings = get_settings_row(db)
    task = AutomationTask(
        task_type=task_type,
        payload_json=json.dumps(payload or {}),
        status="queued",
        priority=priority,
        idempotency_key=idempotency_key,
        attempts=0,
        max_attempts=max_attempts if max_attempts is not None else settings.max_task_attempts,
        scheduled_at=scheduled_at or utcnow(),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(task)
    db.flush()
    log(db, task.id, "info", f"Queued {task_type}")
    return task, True


def log(db: Session, task_id: int, level: str, message: str) -> None:
    db.add(TaskLog(task_id=task_id, level=level, message=message[:1000]))


def claim_next(db: Session) -> AutomationTask | None:
    now = utcnow()
    row = db.execute(
        text(
            """
            UPDATE automation_tasks
            SET status = 'running',
                locked_at = :now,
                attempts = attempts + 1,
                updated_at = :now
            WHERE id = (
                SELECT id FROM automation_tasks
                WHERE status IN ('queued', 'retrying')
                  AND scheduled_at <= :now
                ORDER BY priority DESC, scheduled_at ASC, id ASC
                LIMIT 1
            )
            RETURNING id
            """
        ),
        {"now": now},
    ).first()
    db.commit()
    if row is None:
        return None
    task = db.get(AutomationTask, row[0])
    if task is not None:
        log(db, task.id, "info", f"Claimed attempt {task.attempts}")
        db.commit()
    return task


def mark_succeeded(db: Session, task: AutomationTask, result: dict[str, Any]) -> None:
    task.status = "succeeded"
    task.result_json = json.dumps(result, default=str)[:8000]
    task.last_error = None
    task.locked_at = None
    task.completed_at = utcnow()
    task.updated_at = utcnow()
    log(db, task.id, "info", "Succeeded")


def mark_failed(db: Session, task: AutomationTask, error: str, *, retry: bool = True) -> None:
    message = error[:1000]
    task.last_error = message
    task.locked_at = None
    task.updated_at = utcnow()
    if retry and task.attempts < task.max_attempts:
        delay = min(3600, (2 ** max(task.attempts, 1)) * 15)
        task.status = "retrying"
        task.scheduled_at = utcnow() + timedelta(seconds=delay)
        log(db, task.id, "warning", f"Retry scheduled in {delay}s: {message}")
    else:
        task.status = "failed"
        task.completed_at = utcnow()
        log(db, task.id, "error", f"Failed permanently: {message}")
        write_audit(
            db,
            actor_type="worker",
            action="task.failed",
            entity_type="automation_task",
            entity_id=task.id,
            result="error",
            details={"task_type": task.task_type, "error": message, "attempts": task.attempts},
        )


def recover_stale(db: Session, stale_after_seconds: int = 900) -> int:
    cutoff = utcnow() - timedelta(seconds=stale_after_seconds)
    stale = (
        db.query(AutomationTask)
        .filter(AutomationTask.status == "running", AutomationTask.locked_at.is_not(None), AutomationTask.locked_at < cutoff)
        .all()
    )
    recovered = 0
    for task in stale:
        task.locked_at = None
        task.updated_at = utcnow()
        if task.attempts >= task.max_attempts:
            task.status = "failed"
            task.completed_at = utcnow()
            task.last_error = "Recovered after a crash and attempt limit was already reached."
            log(db, task.id, "error", task.last_error)
        else:
            task.status = "retrying"
            task.scheduled_at = utcnow()
            task.last_error = "Recovered after the worker stopped while the task was running."
            log(db, task.id, "warning", task.last_error)
        recovered += 1
    return recovered


def retry_task(db: Session, task_id: int, actor: str = "user") -> AutomationTask:
    task = db.get(AutomationTask, task_id)
    if task is None:
        raise TaskError("not_found", "Task not found", 404)
    if task.status == "running":
        raise TaskError("task_running", "Cannot retry a task that is currently running", 409)
    if task.status not in {"failed", "cancelled"}:
        raise TaskError("not_retryable", "Only failed or cancelled tasks can be retried", 400)
    if task.attempts >= task.max_attempts:
        task.max_attempts = task.attempts + 1
    task.status = "queued"
    task.scheduled_at = utcnow()
    task.locked_at = None
    task.completed_at = None
    task.updated_at = utcnow()
    log(db, task.id, "info", f"Manual retry requested by {actor}")
    write_audit(
        db,
        actor_type=actor,
        action="task.retry",
        entity_type="automation_task",
        entity_id=task.id,
        result="ok",
        details={"task_type": task.task_type},
    )
    return task


def list_tasks(
    db: Session,
    *,
    status: str | None = None,
    task_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AutomationTask], int]:
    query = db.query(AutomationTask)
    if status:
        query = query.filter(AutomationTask.status == status)
    if task_type:
        query = query.filter(AutomationTask.task_type == task_type)
    total = query.count()
    items = query.order_by(AutomationTask.id.desc()).offset(offset).limit(limit).all()
    return items, total
