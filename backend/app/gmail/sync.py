"""Store Gmail messages and avoid processing the same provider id twice."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.gmail.client import GmailApiError, GmailClient
from app.gmail.parser import looks_like_noise, parse_gmail_message
from app.models import EmailMessage
from app.services.audit import write_audit
from app.services.settings_store import get_settings_row
from app.services.tasks import enqueue
from app.timeutils import utcnow


def connection_status(client: GmailClient | None = None) -> dict:
    from app.gmail.oauth import client_secrets_status, token_file_present

    secrets = client_secrets_status()
    if not secrets["configured"]:
        return {
            "connected": False,
            "email": None,
            "reason": "missing_client_secret",
            "message": "Google OAuth client secrets have not been installed.",
            "secrets_configured": False,
        }
    if not token_file_present() and client is None:
        return {
            "connected": False,
            "email": None,
            "reason": "not_authorized",
            "message": "Gmail has not been authorized.",
            "secrets_configured": True,
        }
    try:
        gmail = client or GmailClient()
        profile = gmail.get_profile()
    except GmailApiError as exc:
        return {
            "connected": False,
            "email": None,
            "reason": exc.code,
            "message": exc.message,
            "secrets_configured": True,
        }
    return {
        "connected": True,
        "email": profile.get("emailAddress"),
        "reason": None,
        "message": "Gmail profile request succeeded.",
        "secrets_configured": True,
        "messages_total": profile.get("messagesTotal"),
        "threads_total": profile.get("threadsTotal"),
    }


def upsert_parsed_message(db: Session, parsed: dict) -> tuple[EmailMessage, bool]:
    provider_id = parsed["provider_message_id"]
    if not provider_id:
        raise GmailApiError("invalid_message", "Gmail message is missing an id.", 502)
    existing = (
        db.query(EmailMessage)
        .filter(EmailMessage.provider == "gmail", EmailMessage.provider_message_id == provider_id)
        .one_or_none()
    )
    if existing is not None:
        return existing, False
    noise = looks_like_noise(parsed)
    row = EmailMessage(
        provider="gmail",
        provider_message_id=provider_id,
        thread_id=parsed.get("thread_id"),
        direction=parsed.get("direction") or "inbound",
        sender=parsed.get("sender"),
        recipients=parsed.get("recipients"),
        subject=parsed.get("subject"),
        snippet=parsed.get("snippet"),
        body_text=parsed.get("body_text"),
        labels_json=json.dumps(parsed.get("labels") or []),
        received_at=parsed.get("received_at"),
        processing_status="ignored" if noise else "unprocessed",
        classification="ignored" if noise else None,
        processed_at=utcnow() if noise else None,
        is_synthetic=False,
    )
    db.add(row)
    db.flush()
    if not noise and row.direction == "inbound":
        enqueue(
            db,
            task_type="process_email",
            payload={"email_message_id": row.id},
            idempotency_key=f"process_email:{provider_id}",
            priority=5,
        )
    return row, True


def sync_inbox(db: Session, *, max_results: int = 15, client: GmailClient | None = None) -> dict:
    settings = get_settings_row(db)
    gmail = client or GmailClient()
    try:
        listed = gmail.list_messages(settings.gmail_search_query, max_results=max_results)
    except GmailApiError:
        raise
    created = 0
    existing = 0
    ignored = 0
    errors: list[str] = []
    for item in listed:
        message_id = item.get("id")
        if not message_id:
            continue
        try:
            full = gmail.get_message(message_id)
            parsed = parse_gmail_message(full)
            row, was_created = upsert_parsed_message(db, parsed)
        except GmailApiError as exc:
            errors.append(f"{message_id}: {exc.code}")
            continue
        if was_created:
            created += 1
            if row.processing_status == "ignored":
                ignored += 1
        else:
            existing += 1
    write_audit(
        db,
        actor_type="system",
        action="gmail.sync",
        entity_type="email_message",
        entity_id=None,
        result="ok" if not errors else "partial",
        details={"created": created, "existing": existing, "ignored": ignored, "errors": errors[:10], "query": settings.gmail_search_query},
    )
    return {
        "fetched": len(listed),
        "created": created,
        "already_stored": existing,
        "ignored": ignored,
        "errors": errors,
        "query": settings.gmail_search_query,
    }
