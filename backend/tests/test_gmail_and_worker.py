import base64
import json
from datetime import timedelta

from app.gmail.client import GmailApiError, build_raw_message
from app.gmail.parser import looks_like_noise, parse_gmail_message
from app.gmail.sync import upsert_parsed_message
from app.services.pipeline import MailFlowError, send_draft
from app.services.tasks import claim_next, enqueue, mark_failed, recover_stale
from app.timeutils import utcnow


def _gmail_payload(message_id="abc", subject="Blinds quote", sender="Aisha <aisha.rahman.example@example.com>", body="Need motorized blinds in Dubai Marina"):
    encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return {
        "id": message_id,
        "threadId": "thread-1",
        "snippet": body[:40],
        "internalDate": "1710000000000",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": "sales@example.com"},
                {"name": "Subject", "value": subject},
            ],
            "body": {"data": encoded},
        },
    }


def test_parser_reads_plain_text_and_ignores_newsletters():
    parsed = parse_gmail_message(_gmail_payload())
    assert "motorized blinds" in parsed["body_text"]
    assert parsed["direction"] == "inbound"
    assert looks_like_noise(parsed) is False
    noisy = parse_gmail_message(
        _gmail_payload(message_id="news", subject="Weekly newsletter", sender="news@noreply.example.com", body="Unsubscribe here")
    )
    noisy["list_unsubscribe"] = "<mailto:unsubscribe@example.com>"
    assert looks_like_noise(noisy) is True


def test_duplicate_provider_message_is_not_stored_twice(client, auth_header):
    from app.database import SessionLocal
    from app.gmail.parser import parse_gmail_message

    db = SessionLocal()
    try:
        parsed = parse_gmail_message(_gmail_payload())
        first, created_first = upsert_parsed_message(db, parsed)
        second, created_second = upsert_parsed_message(db, parsed)
        db.commit()
        assert created_first is True
        assert created_second is False
        assert first.id == second.id
    finally:
        db.close()


def test_gmail_status_is_not_connected_without_oauth(client, auth_header):
    response = client.get("/api/v1/gmail/status", headers=auth_header)
    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is False
    assert body["reason"] == "missing_client_secret"


def test_send_failure_is_recorded(client, auth_header, monkeypatch):
    class BrokenGmail:
        def __init__(self, service=None):
            pass

        def get_profile(self):
            return {"emailAddress": "owner@example.com"}

        def send_raw(self, raw):
            raise GmailApiError("gmail_api_error", "Gmail rejected the message.", 502)

    monkeypatch.setattr("app.services.pipeline.GmailClient", BrokenGmail)
    created = client.post(
        "/api/v1/drafts",
        headers=auth_header,
        json={"recipient": "buyer@example.com", "subject": "Quote", "body": "Hello from SalesPilot"},
    )
    assert created.status_code == 201
    draft_id = created.json()["id"]
    sent = client.post(
        f"/api/v1/drafts/{draft_id}/send",
        headers=auth_header,
        json={"confirm": True, "recipient": "buyer@example.com"},
    )
    assert sent.status_code == 502
    assert sent.json()["detail"]["code"] == "gmail_api_error"
    stored = client.get("/api/v1/drafts", headers=auth_header).json()
    match = next(item for item in stored if item["id"] == draft_id)
    assert match["status"] in {"send_failed", "send_unknown"}
    assert match["send_error"]


def test_unapproved_agent_send_is_blocked(client, auth_header):
    from app.database import SessionLocal
    from app.agent.tools import execute_tool

    created = client.post(
        "/api/v1/drafts",
        headers=auth_header,
        json={"recipient": "buyer@example.com", "subject": "Quote", "body": "Hello"},
    )
    db = SessionLocal()
    try:
        result = execute_tool(db, "send_approved_email", {"draft_id": created.json()["id"]})
        assert result["ok"] is False
        assert result["error"] == "approval_required"
    finally:
        db.close()


def test_repeat_send_does_not_call_provider_again(client, auth_header):
    from app.database import SessionLocal
    from app.models import EmailDraft

    db = SessionLocal()
    try:
        draft = EmailDraft(
            recipient="buyer@example.com",
            subject="Quote",
            body="Hello",
            status="provider_accepted",
            approval_state="approved",
            provider_message_id="gmail-msg-1",
        )
        db.add(draft)
        db.commit()
        result = send_draft(db, draft.id, user_confirmed=True, expected_recipient="buyer@example.com")
        assert result["already_sent"] is True
        assert result["provider_message_id"] == "gmail-msg-1"
    finally:
        db.close()


def test_worker_runs_due_task_once(client):
    from app.database import SessionLocal
    from app.models import AutomationTask
    from app.services.settings_store import get_settings_row
    from app.worker.loop import process_once

    db = SessionLocal()
    try:
        settings = get_settings_row(db)
        settings.inbox_poll_enabled = False
        settings.follow_up_scan_enabled = False
        task, created = enqueue(db, task_type="follow_up_scan", payload={}, idempotency_key="scan-once", priority=10)
        db.commit()
        task_id = task.id
        assert created is True
    finally:
        db.close()

    assert process_once() is True
    assert process_once() is False
    db = SessionLocal()
    try:
        stored = db.get(AutomationTask, task_id)
        assert stored.status == "succeeded"
        again, created_again = enqueue(db, task_type="follow_up_scan", payload={}, idempotency_key="scan-once")
        assert created_again is False
        assert again.id == task_id
    finally:
        db.close()


def test_retry_backoff_and_startup_recovery(client):
    from app.database import SessionLocal
    from app.models import AutomationTask
    from app.services.settings_store import get_settings_row

    db = SessionLocal()
    try:
        settings = get_settings_row(db)
        settings.inbox_poll_enabled = False
        settings.follow_up_scan_enabled = False
        task, _ = enqueue(db, task_type="follow_up_scan", payload={}, idempotency_key="retry-me", max_attempts=3)
        task.status = "running"
        task.attempts = 1
        task.locked_at = utcnow()
        mark_failed(db, task, "temporary network error", retry=True)
        db.commit()
        assert task.status == "retrying"
        assert task.scheduled_at > utcnow()
        assert claim_next(db) is None

        task.scheduled_at = utcnow() - timedelta(seconds=5)
        task.status = "running"
        task.locked_at = utcnow() - timedelta(hours=2)
        task.attempts = 1
        task.max_attempts = 3
        db.commit()
        recovered = recover_stale(db, stale_after_seconds=60)
        db.commit()
        assert recovered == 1
        db.refresh(task)
        assert task.status == "retrying"
    finally:
        db.close()


def test_worker_shutdown(client):
    import threading

    from app.database import SessionLocal
    from app.models import WorkerHeartbeat
    from app.services.settings_store import get_settings_row
    from app.worker.loop import run_forever

    db = SessionLocal()
    try:
        settings = get_settings_row(db)
        settings.inbox_poll_enabled = False
        settings.follow_up_scan_enabled = False
        db.commit()
    finally:
        db.close()

    stop = threading.Event()
    thread = threading.Thread(target=run_forever, args=(stop,), kwargs={"tick_seconds": 0.05})
    thread.start()
    stop.set()
    thread.join(5)
    assert thread.is_alive() is False
    db = SessionLocal()
    try:
        beat = db.get(WorkerHeartbeat, 1)
        assert beat.status == "stopped"
    finally:
        db.close()


def test_build_raw_message_includes_draft_marker():
    raw = build_raw_message(sender="sales@example.com", recipient="buyer@example.com", subject="Hello", body="Body", draft_id=42)
    decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
    assert "X-SalesPilot-Draft: 42" in decoded
    assert "buyer@example.com" in decoded
