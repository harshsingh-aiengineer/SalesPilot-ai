import time

from app.agent.runner import honesty_guard
from app.agent.tools import execute_tool
from app.llm import LlmError


class DummyLock:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_unregistered_tool_is_rejected(client, auth_header):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        result = execute_tool(db, "drop_table", {"sql": "delete from leads"})
        assert result["ok"] is False
        assert result["error"] == "unregistered_tool"
    finally:
        db.close()


def test_search_tool_reads_real_rows(client, auth_header):
    client.post("/api/v1/leads", headers=auth_header, json={"name": "Tool Lead", "priority": "hot", "status": "new"})
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        result = execute_tool(db, "search_leads", {"query": "Tool Lead"})
        assert result["ok"] is True
        assert result["total"] == 1
        assert result["leads"][0]["name"] == "Tool Lead"
    finally:
        db.close()


def test_invalid_create_arguments_do_not_write(client, auth_header):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        result = execute_tool(db, "create_lead", {"email": "not-an-email"})
        assert result["ok"] is False
        assert result["error"] == "invalid_arguments"
        db.commit()
    finally:
        db.close()
    listed = client.get("/api/v1/leads", headers=auth_header)
    assert listed.json()["total"] == 0


def test_update_missing_lead_reports_not_found(client, auth_header):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        result = execute_tool(db, "update_lead", {"lead_id": 404, "status": "won"})
        assert result["ok"] is False
        assert result["error"] == "not_found"
    finally:
        db.close()


def test_honesty_guard_blocks_fabricated_send():
    text = honesty_guard("I sent the email successfully.", {"send": False, "create_lead": False})
    assert "No email was sent" in text


def test_agent_executes_tool_then_answers(client, auth_header, monkeypatch):
    client.post("/api/v1/leads", headers=auth_header, json={"name": "Marina Hot", "priority": "hot", "location": "Dubai Marina"})
    calls = {"n": 0}

    def fake_chat(messages, tools=None, force_json=False):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "search_leads", "arguments": {"query": "Marina"}}}],
                }
            }
        return {"message": {"content": "The search tool returned Marina Hot.", "tool_calls": []}}

    monkeypatch.setattr("app.agent.runner.chat", fake_chat)
    monkeypatch.setattr("app.agent.runner.LlmLock", DummyLock)
    created = client.post("/api/v1/conversations", headers=auth_header, json={"title": "Find Marina"})
    conversation_id = created.json()["id"]
    accepted = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=auth_header,
        json={"content": "Find the Marina lead."},
    )
    assert accepted.status_code == 202
    run_id = accepted.json()["run"]["id"]
    final = None
    for _ in range(50):
        current = client.get(f"/api/v1/runs/{run_id}", headers=auth_header)
        if current.json()["status"] not in {"queued", "running"}:
            final = current.json()
            break
        time.sleep(0.05)
    assert final is not None
    assert final["status"] == "succeeded"
    assert final["steps"][0]["tool_name"] == "search_leads"
    assert "Marina Hot" in final["steps"][0]["result_summary"]
    assert "Marina Hot" in final["result_summary"]


def test_agent_reports_ollama_outage(client, auth_header, monkeypatch):
    def fake_chat(messages, tools=None, force_json=False):
        raise LlmError("ollama_unavailable", "Ollama is not reachable at http://ollama:11434")

    monkeypatch.setattr("app.agent.runner.chat", fake_chat)
    monkeypatch.setattr("app.agent.runner.LlmLock", DummyLock)
    conversation_id = client.post("/api/v1/conversations", headers=auth_header, json={}).json()["id"]
    run_id = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=auth_header,
        json={"content": "Summarize the pipeline."},
    ).json()["run"]["id"]
    final = None
    for _ in range(50):
        current = client.get(f"/api/v1/runs/{run_id}", headers=auth_header).json()
        if current["status"] not in {"queued", "running"}:
            final = current
            break
        time.sleep(0.05)
    assert final["status"] == "failed"
    assert "Ollama" in final["error_message"]
    assert final["result_summary"] is None


def test_agent_stops_at_tool_limit(client, auth_header, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_agent_tool_calls", 2)

    def fake_chat(messages, tools=None, force_json=False):
        if tools is None:
            return {"message": {"content": "Stopped after tools.", "tool_calls": []}}
        return {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "get_sales_summary", "arguments": {}}}],
            }
        }

    monkeypatch.setattr("app.agent.runner.chat", fake_chat)
    monkeypatch.setattr("app.agent.runner.LlmLock", DummyLock)
    conversation_id = client.post("/api/v1/conversations", headers=auth_header, json={}).json()["id"]
    run_id = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=auth_header,
        json={"content": "Keep going."},
    ).json()["run"]["id"]
    final = None
    for _ in range(50):
        current = client.get(f"/api/v1/runs/{run_id}", headers=auth_header).json()
        if current["status"] not in {"queued", "running"}:
            final = current
            break
        time.sleep(0.05)
    assert final["status"] == "incomplete"
    assert len(final["steps"]) == 2
    assert "Tool-call limit" in (final["error_message"] or "")


def test_cancel_queued_run(client, auth_header, monkeypatch):
    started = {"go": False}

    def fake_chat(messages, tools=None, force_json=False):
        started["go"] = True
        return {"message": {"content": "too late", "tool_calls": []}}

    monkeypatch.setattr("app.agent.runner.chat", fake_chat)
    monkeypatch.setattr("app.agent.runner.LlmLock", DummyLock)
    monkeypatch.setattr("app.api.agent_routes._start", lambda run_id: None)
    conversation_id = client.post("/api/v1/conversations", headers=auth_header, json={}).json()["id"]
    run_id = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=auth_header,
        json={"content": "Wait."},
    ).json()["run"]["id"]
    cancelled = client.post(f"/api/v1/runs/{run_id}/cancel", headers=auth_header)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert started["go"] is False
