"""Health, authentication, and integration status."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.api.deps import bearer, issue_session, password_matches, require_user
from app.config import get_settings
from app.database import get_db
from app.llm import model_ready, probe_model
from app.models import AppSession, WorkerHeartbeat
from app.schemas import LoginRequest, LoginResponse
from app.timeutils import utcnow

router = APIRouter()


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    settings = get_settings()
    return {
        "status": "ok",
        "service": "salespilot-api",
        "version": __version__,
        "auth_required": settings.salespilot_auth_required,
        "dev_mode": settings.salespilot_dev_mode,
        "insecure_default_password": settings.using_default_password,
    }


@router.get("/ready")
def ready(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    ai = model_ready()
    return {"status": "ready", "database": "ok", "ollama": ai}


@router.post("/auth/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    settings = get_settings()
    if not settings.salespilot_password:
        raise HTTPException(
            status_code=503,
            detail={"code": "auth_not_configured", "message": "Set SALESPILOT_PASSWORD before signing in."},
        )
    if not password_matches(body.password):
        raise HTTPException(status_code=401, detail={"code": "invalid_password", "message": "Password was not accepted."})
    token, row = issue_session(db)
    return LoginResponse(token=token, expires_at=row.expires_at)


@router.post("/auth/logout")
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
    _: str = Depends(require_user),
) -> dict:
    if credentials is not None:
        from app.api.deps import hash_token

        db.query(AppSession).filter(AppSession.token_hash == hash_token(credentials.credentials)).delete()
        db.commit()
    return {"ok": True}


@router.get("/auth/me")
def me(_: str = Depends(require_user)) -> dict:
    settings = get_settings()
    return {
        "user": "local",
        "auth_required": settings.salespilot_auth_required,
        "dev_mode": settings.salespilot_dev_mode,
        "insecure_default_password": settings.using_default_password,
    }


@router.get("/settings/status")
def settings_status(db: Session = Depends(get_db), _: str = Depends(require_user)) -> dict:
    from app.gmail.sync import connection_status
    from app.models import EmailMessage
    from app.services.settings_store import get_settings_row

    settings = get_settings()
    ai = model_ready()
    gmail = connection_status()
    heartbeat = db.get(WorkerHeartbeat, 1)
    worker_fresh = bool(
        heartbeat
        and heartbeat.last_seen_at
        and heartbeat.status == "running"
        and (utcnow() - heartbeat.last_seen_at).total_seconds() < 30
    )
    last_email = db.query(EmailMessage).order_by(EmailMessage.id.desc()).first()
    app_settings = get_settings_row(db)
    return {
        "ollama": ai,
        "gmail": gmail,
        "worker": {
            "running": worker_fresh,
            "status": heartbeat.status if heartbeat else "stopped",
            "last_seen_at": heartbeat.last_seen_at.isoformat() if heartbeat and heartbeat.last_seen_at else None,
            "message": heartbeat.message if heartbeat else "Worker has not started",
        },
        "inbox_query": app_settings.gmail_search_query,
        "last_stored_email_at": last_email.created_at.isoformat() if last_email else None,
        "model": settings.ollama_model,
        "ollama_base_url": settings.ollama_base_url,
        "business_timezone": settings.business_timezone,
        "dev_mode": settings.salespilot_dev_mode,
        "insecure_default_password": settings.using_default_password,
        "max_concurrent_llm": settings.max_concurrent_llm,
        "test_recipient_configured": bool(settings.gmail_test_recipient),
        "client_secrets_path": str(settings.client_secrets_path.resolve()),
        "installed_models": ai.get("installed_models") or [],
    }


@router.post("/settings/model-check")
def model_check(_: str = Depends(require_user)) -> dict:
    return probe_model()
