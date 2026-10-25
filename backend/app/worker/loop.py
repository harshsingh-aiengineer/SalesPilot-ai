"""One worker loop. Scheduling lives here, not in the API process."""

from __future__ import annotations

import logging
import threading

from app.database import SessionLocal
from app.models import WorkerHeartbeat
from app.services.tasks import claim_next, log, mark_failed, mark_succeeded, recover_stale
from app.timeutils import utcnow
from app.worker.handlers import HANDLERS, NonRetryable, schedule_periodic

logger = logging.getLogger(__name__)


def _heartbeat(status: str, task_id: int | None = None, message: str | None = None) -> None:
    db = SessionLocal()
    try:
        row = db.get(WorkerHeartbeat, 1)
        if row is None:
            row = WorkerHeartbeat(id=1)
            db.add(row)
        row.status = status
        row.last_seen_at = utcnow()
        row.current_task_id = task_id
        row.message = message
        if row.started_at is None and status == "running":
            row.started_at = utcnow()
        db.commit()
    finally:
        db.close()


def process_once() -> bool:
    """Claim and run at most one due task. Returns True when a task was claimed."""
    db = SessionLocal()
    try:
        recover_stale(db)
        schedule_periodic(db)
        db.commit()
        task = claim_next(db)
        if task is None:
            return False
        _heartbeat("running", task.id, task.task_type)
        handler = HANDLERS.get(task.task_type)
        try:
            if handler is None:
                raise NonRetryable(f"No handler for {task.task_type}.")
            result = handler(db, task)
            if result.get("deferred"):
                log(db, task.id, "info", "Deferred")
                db.commit()
                return True
            mark_succeeded(db, task, result)
            db.commit()
            return True
        except NonRetryable as exc:
            mark_failed(db, task, str(exc), retry=False)
            db.commit()
            logger.info("task %s not retryable: %s", task.id, exc)
            return True
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            failed = db.get(type(task), task.id)
            if failed is not None:
                mark_failed(db, failed, f"{exc.__class__.__name__}: {exc}", retry=True)
                db.commit()
            logger.exception("task %s failed", task.id)
            return True
    finally:
        db.close()


def run_forever(stop: threading.Event, tick_seconds: float = 2.0) -> None:
    logger.info("worker started")
    _heartbeat("running", None, "Worker started")
    try:
        while not stop.is_set():
            try:
                worked = process_once()
            except Exception:  # noqa: BLE001
                logger.exception("worker tick failed")
                worked = False
            _heartbeat("running", None, "Idle" if not worked else "Processed a task")
            stop.wait(0.2 if worked else tick_seconds)
    finally:
        _heartbeat("stopped", None, "Worker stopped")
        logger.info("worker stopped")
