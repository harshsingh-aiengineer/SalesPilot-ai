"""Singleton automation settings and default policy rules."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import AppSetting, AutomationRule, ResourceLock, WorkerHeartbeat
from app.timeutils import utcnow

DEFAULT_RULES = [
    {
        "name": "Classify inbox messages",
        "trigger_type": "gmail_poll",
        "enabled": True,
        "requires_approval": False,
        "conditions": {"scope": "configured_query"},
        "actions": {"action": "classify"},
    },
    {
        "name": "Create or update CRM leads",
        "trigger_type": "email_classified",
        "enabled": True,
        "requires_approval": False,
        "conditions": {"min_confidence": 0.6, "duplicate_check": True},
        "actions": {"action": "upsert_lead"},
    },
    {
        "name": "Generate email drafts",
        "trigger_type": "lead_missing_information",
        "enabled": True,
        "requires_approval": False,
        "conditions": {},
        "actions": {"action": "save_draft"},
    },
    {
        "name": "Send customer email",
        "trigger_type": "draft_approved",
        "enabled": True,
        "requires_approval": True,
        "conditions": {"auto_send": False, "allowlist_required": True},
        "actions": {"action": "send_email"},
    },
]


def get_settings_row(db: Session) -> AppSetting:
    row = db.get(AppSetting, 1)
    if row is None:
        row = AppSetting(id=1)
        db.add(row)
        db.flush()
    return row


def ensure_defaults(db: Session) -> AppSetting:
    settings = get_settings_row(db)
    existing = {rule.name for rule in db.query(AutomationRule).all()}
    for item in DEFAULT_RULES:
        if item["name"] in existing:
            continue
        db.add(
            AutomationRule(
                name=item["name"],
                enabled=item["enabled"],
                trigger_type=item["trigger_type"],
                conditions_json=json.dumps(item["conditions"]),
                actions_json=json.dumps(item["actions"]),
                requires_approval=item["requires_approval"],
            )
        )
    if db.get(ResourceLock, "llm") is None:
        db.add(ResourceLock(name="llm", holder=None, expires_at=None))
    if db.get(WorkerHeartbeat, 1) is None:
        db.add(WorkerHeartbeat(id=1, status="stopped", message="Worker has not started"))
    db.flush()
    return settings


def touch_settings(row: AppSetting) -> None:
    row.updated_at = utcnow()
