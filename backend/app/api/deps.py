"""Authentication dependency for the single-user local deployment."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AppSession
from app.timeutils import utcnow

bearer = HTTPBearer(auto_error=False)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_session(db: Session) -> tuple[str, object]:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    row = AppSession(token_hash=hash_token(token), expires_at=utcnow() + timedelta(hours=settings.token_ttl_hours))
    db.add(row)
    db.commit()
    db.refresh(row)
    return token, row


def require_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> str:
    settings = get_settings()
    if not settings.salespilot_auth_required:
        if not settings.salespilot_dev_mode:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "auth_misconfigured",
                    "message": "Authentication can be disabled only when SALESPILOT_DEV_MODE=true.",
                },
            )
        return "dev-user"
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Sign in required."})
    row = (
        db.query(AppSession)
        .filter(AppSession.token_hash == hash_token(credentials.credentials), AppSession.expires_at > utcnow())
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Sign in required."})
    return "local-user"


def password_matches(provided: str) -> bool:
    expected = get_settings().salespilot_password
    if not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
