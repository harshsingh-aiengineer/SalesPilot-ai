"""Audit log writes with secret redaction."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog
from app.redact import redact


def write_audit(
    db: Session,
    *,
    actor_type: str,
    action: str,
    entity_type: str,
    entity_id: str | int | None,
    result: str,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    row = AuditLog(
        actor_type=actor_type,
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        result=result,
        details_json=json.dumps(redact(details or {}), default=str),
    )
    db.add(row)
    return row
