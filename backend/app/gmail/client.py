"""Thin Gmail API wrapper. Callers must treat failures as failures."""

from __future__ import annotations

import base64
from email.message import EmailMessage

from app.gmail.oauth import GmailAuthError, load_credentials


class GmailApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 502):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def _service(injected=None):
    if injected is not None:
        return injected
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover
        raise GmailApiError("gmail_library_missing", "Google API libraries are not installed.", 500) from exc
    try:
        creds = load_credentials()
    except GmailAuthError as exc:
        raise GmailApiError(exc.code, exc.message, exc.status) from exc
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _translate(exc: Exception) -> GmailApiError:
    status = getattr(getattr(exc, "resp", None), "status", None)
    text = str(exc)
    if status in {401, 403} or "invalid_grant" in text or "unauthorized" in text.lower():
        return GmailApiError(
            "gmail_unauthorized",
            "Gmail rejected the credentials. The grant may have expired or been revoked. Authorize the account again.",
            401,
        )
    if status == 404:
        return GmailApiError("gmail_not_found", "Gmail could not find that message.", 404)
    if status == 429:
        return GmailApiError("gmail_quota", "Gmail API quota was exceeded. Try again later.", 429)
    return GmailApiError("gmail_api_error", f"Gmail API request failed ({status or exc.__class__.__name__}).", 502)


class GmailClient:
    def __init__(self, service=None):
        self.service = _service(service)

    def get_profile(self) -> dict:
        try:
            return self.service.users().getProfile(userId="me").execute()
        except GmailApiError:
            raise
        except Exception as exc:
            raise _translate(exc) from exc

    def list_messages(self, query: str, max_results: int = 15) -> list[dict]:
        try:
            result = (
                self.service.users()
                .messages()
                .list(userId="me", q=query, maxResults=max(1, min(max_results, 50)))
                .execute()
            )
        except Exception as exc:
            raise _translate(exc) from exc
        return result.get("messages") or []

    def get_message(self, message_id: str) -> dict:
        try:
            return self.service.users().messages().get(userId="me", id=message_id, format="full").execute()
        except Exception as exc:
            raise _translate(exc) from exc

    def send_raw(self, raw: str) -> dict:
        try:
            return self.service.users().messages().send(userId="me", body={"raw": raw}).execute()
        except Exception as exc:
            raise _translate(exc) from exc

    def create_draft(self, raw: str) -> dict:
        try:
            return (
                self.service.users()
                .drafts()
                .create(userId="me", body={"message": {"raw": raw}})
                .execute()
            )
        except Exception as exc:
            raise _translate(exc) from exc

    def list_sent_matching(self, recipient: str, subject: str) -> list[dict]:
        query = f'in:sent to:{recipient} subject:"{subject}" newer_than:2d'
        return self.list_messages(query, max_results=10)


def build_raw_message(*, sender: str | None, recipient: str, subject: str, body: str, draft_id: int) -> str:
    message = EmailMessage()
    if sender:
        message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message["X-SalesPilot-Draft"] = str(draft_id)
    message.set_content(body)
    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    return encoded
