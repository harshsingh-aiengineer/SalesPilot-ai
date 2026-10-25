"""Google OAuth 2.0 for the Gmail API. Tokens stay on disk and never go to the browser."""

from __future__ import annotations

import json
import os
import secrets
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import OAuthState
from app.timeutils import utcnow

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
]


class GmailAuthError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def restrict_permissions(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        return


def client_secrets_status() -> dict:
    path = get_settings().client_secrets_path
    return {
        "configured": path.is_file(),
        "path_set": True,
        "filename": path.name,
    }


def load_credentials():
    settings = get_settings()
    token_path = settings.token_path
    if not token_path.is_file():
        raise GmailAuthError("not_authorized", "Gmail has not been authorized yet.", 409)
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError as exc:  # pragma: no cover
        raise GmailAuthError("gmail_library_missing", "Google API libraries are not installed.", 500) from exc
    creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            raise GmailAuthError(
                "refresh_failed",
                "Gmail credentials could not be refreshed. Authorize the account again.",
                401,
            ) from exc
        save_credentials(creds)
    if not creds.valid:
        raise GmailAuthError(
            "credentials_invalid",
            "Gmail credentials are invalid or revoked. Authorize the account again.",
            401,
        )
    return creds


def save_credentials(creds) -> None:
    settings = get_settings()
    path = settings.token_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")
    restrict_permissions(path)


def create_state(db: Session) -> str:
    state = secrets.token_urlsafe(32)
    db.add(OAuthState(state=state, created_at=utcnow(), expires_at=utcnow() + timedelta(minutes=15)))
    db.commit()
    return state


def consume_state(db: Session, state: str) -> None:
    row = db.query(OAuthState).filter(OAuthState.state == state).one_or_none()
    now = utcnow()
    if row is None or row.consumed_at is not None or row.expires_at < now:
        raise GmailAuthError("invalid_oauth_state", "The authorization attempt expired or was not recognized.", 400)
    row.consumed_at = now
    db.commit()


def build_flow(state: str):
    settings = get_settings()
    secrets_path = settings.client_secrets_path
    if not secrets_path.is_file():
        raise GmailAuthError(
            "missing_client_secret",
            "Google OAuth client secrets are not installed. Add the downloaded client JSON to the secrets directory.",
            409,
        )
    try:
        payload = json.loads(secrets_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GmailAuthError("invalid_client_secret", "The Google client secret file is not valid JSON.", 500) from exc
    if "web" not in payload and "installed" not in payload:
        raise GmailAuthError(
            "invalid_client_secret",
            "The client secret file must be an OAuth client of type Web application or Desktop.",
            500,
        )
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(str(secrets_path), scopes=GMAIL_SCOPES, state=state)
    flow.redirect_uri = settings.gmail_oauth_redirect_uri
    return flow


def authorization_url(db: Session) -> str:
    state = create_state(db)
    flow = build_flow(state)
    url, _returned_state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return url


def exchange_code(db: Session, code: str, state: str) -> str:
    consume_state(db, state)
    flow = build_flow(state)
    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        raise GmailAuthError("oauth_exchange_failed", f"Google rejected the authorization code: {exc.__class__.__name__}", 400) from exc
    creds = flow.credentials
    save_credentials(creds)
    return "authorized"


def disconnect() -> None:
    path = get_settings().token_path
    if path.is_file():
        path.unlink()


def token_file_present() -> bool:
    return get_settings().token_path.is_file()
