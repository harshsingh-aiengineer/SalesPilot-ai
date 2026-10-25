"""Automation settings, tasks, and audit log."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require_user
from app.database import get_db
from app.models import AuditLog, AutomationRule
from app.schemas import AuditOut, RuleOut, SettingsIn, SettingsOut, TaskList, TaskLogOut, TaskOut
from app.services.settings_store import get_settings_row, touch_settings
from app.services.tasks import TaskError, list_tasks, retry_task

router = APIRouter()


@router.get("/automation/settings", response_model=SettingsOut)
def read_settings(db: Session = Depends(get_db), _: str = Depends(require_user)) -> SettingsOut:
    return SettingsOut.model_validate(get_settings_row(db))


@router.put("/automation/settings", response_model=SettingsOut)
def write_settings(body: SettingsIn, db: Session = Depends(get_db), _: str = Depends(require_user)) -> SettingsOut:
    if body.auto_send_enabled and not body.allowed_recipient_scope.strip():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "allowlist_required",
                "message": "Automatic sending requires an allowed recipient scope.",
            },
        )
    row = get_settings_row(db)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    touch_settings(row)
    rule_map = {
        "Classify inbox messages": body.inbox_poll_enabled,
        "Create or update CRM leads": body.lead_creation_enabled,
        "Generate email drafts": body.draft_generation_enabled,
        "Send customer email": True,
    }
    for rule in db.query(AutomationRule).all():
        if rule.name in rule_map:
            rule.enabled = rule_map[rule.name]
        if rule.name == "Send customer email":
            rule.requires_approval = body.send_requires_approval
            rule.conditions_json = (
                '{"auto_send": %s, "allowlist_required": true}' % ("true" if body.auto_send_enabled else "false")
            )
    db.commit()
    db.refresh(row)
    return SettingsOut.model_validate(row)


@router.get("/automation/rules", response_model=list[RuleOut])
def read_rules(db: Session = Depends(get_db), _: str = Depends(require_user)) -> list[RuleOut]:
    rows = db.query(AutomationRule).order_by(AutomationRule.id.asc()).all()
    return [RuleOut.model_validate(row) for row in rows]


@router.get("/tasks", response_model=TaskList)
def get_tasks(
    status: str | None = None,
    task_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
) -> TaskList:
    items, total = list_tasks(db, status=status, task_type=task_type, limit=limit, offset=offset)
    return TaskList(items=[TaskOut.model_validate(item) for item in items], total=total)


@router.post("/tasks/{task_id}/retry", response_model=TaskOut)
def retry(task_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)) -> TaskOut:
    try:
        task = retry_task(db, task_id, actor="user")
    except TaskError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message}) from exc
    db.commit()
    db.refresh(task)
    return TaskOut.model_validate(task)


@router.get("/tasks/{task_id}/logs", response_model=list[TaskLogOut])
def task_logs(task_id: int, db: Session = Depends(get_db), _: str = Depends(require_user)) -> list[TaskLogOut]:
    from app.models import AutomationTask, TaskLog

    task = db.get(AutomationTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Task not found."})
    rows = db.query(TaskLog).filter(TaskLog.task_id == task_id).order_by(TaskLog.id.asc()).all()
    return [TaskLogOut.model_validate(row) for row in rows]


@router.get("/audit", response_model=list[AuditOut])
def audit(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
) -> list[AuditOut]:
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all()
    return [AuditOut.model_validate(row) for row in rows]
