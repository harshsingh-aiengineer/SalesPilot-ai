def test_health_does_not_require_auth(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "salespilot-api"


def test_login_rejects_bad_password(client):
    response = client.post("/api/v1/auth/login", json={"password": "nope"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_password"


def test_leads_require_auth(client):
    response = client.get("/api/v1/leads")
    assert response.status_code == 401


def test_lead_crud_search_and_delete(client, auth_header):
    created = client.post(
        "/api/v1/leads",
        headers=auth_header,
        json={
            "name": "Test Lead",
            "email": "test.lead@example.com",
            "location": "Dubai Marina",
            "requirements": "Motorized blinds",
            "status": "new",
            "priority": "warm",
            "budget_min": 1000,
            "budget_max": 2000,
        },
    )
    assert created.status_code == 201, created.text
    lead_id = created.json()["id"]

    updated = client.patch(
        f"/api/v1/leads/{lead_id}",
        headers=auth_header,
        json={"status": "contacted", "phone": "+971-50-000-0000"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "contacted"

    searched = client.get("/api/v1/leads", headers=auth_header, params={"q": "Marina"})
    assert searched.status_code == 200
    assert searched.json()["total"] == 1

    note = client.post(f"/api/v1/leads/{lead_id}/notes", headers=auth_header, json={"body": "Called the customer."})
    assert note.status_code == 201

    deleted = client.delete(f"/api/v1/leads/{lead_id}", headers=auth_header)
    assert deleted.status_code == 200
    missing = client.get(f"/api/v1/leads/{lead_id}", headers=auth_header)
    assert missing.status_code == 404


def test_lead_persists_across_restart(db_url, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as first:
        login = first.post("/api/v1/auth/login", json={"password": "test-password"})
        header = {"Authorization": f"Bearer {login.json()['token']}"}
        created = first.post("/api/v1/leads", headers=header, json={"name": "Persistent Lead", "email": "persist@example.com"})
        assert created.status_code == 201
        lead_id = created.json()["id"]

    from app.config import reset_settings_cache
    from app.database import configure_engine

    reset_settings_cache()
    configure_engine(db_url)
    with TestClient(create_app()) as second:
        login = second.post("/api/v1/auth/login", json={"password": "test-password"})
        header = {"Authorization": f"Bearer {login.json()['token']}"}
        fetched = second.get(f"/api/v1/leads/{lead_id}", headers=header)
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "Persistent Lead"


def test_duplicate_post_is_rejected_for_same_idempotency_key(client, auth_header):
    headers = {**auth_header, "Idempotency-Key": "lead-create-1"}
    first = client.post("/api/v1/leads", headers=headers, json={"name": "Once"})
    second = client.post("/api/v1/leads", headers=headers, json={"name": "Once"})
    conflict = client.post("/api/v1/leads", headers=headers, json={"name": "Different"})
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert conflict.status_code == 409


def test_bulk_delete_requires_confirmation(client, auth_header):
    created = client.post("/api/v1/leads", headers=auth_header, json={"name": "Bulk"})
    lead_id = created.json()["id"]
    denied = client.post("/api/v1/leads/bulk", headers=auth_header, json={"action": "delete", "ids": [lead_id], "confirm": False})
    assert denied.status_code == 400
    allowed = client.post("/api/v1/leads/bulk", headers=auth_header, json={"action": "delete", "ids": [lead_id], "confirm": True})
    assert allowed.status_code == 200
    assert client.get(f"/api/v1/leads/{lead_id}", headers=auth_header).status_code == 404


def test_email_foreign_key_is_enforced(db_url):
    from app.database import SessionLocal
    from app.models import EmailMessage

    db = SessionLocal()
    db.add(
        EmailMessage(
            provider="gmail",
            provider_message_id="m-1",
            lead_id=99999,
            sender="a@example.com",
            subject="Hello",
        )
    )
    try:
        db.commit()
        raised = False
    except Exception:
        db.rollback()
        raised = True
    finally:
        db.close()
    assert raised


def test_crm_works_when_ollama_is_down(client, auth_header):
    created = client.post("/api/v1/leads", headers=auth_header, json={"name": "Offline AI", "requirements": "Curtains"})
    assert created.status_code == 201
    status = client.get("/api/v1/settings/status", headers=auth_header)
    assert status.status_code == 200
    assert status.json()["ollama"]["reachable"] is False
    assert status.json()["gmail"]["connected"] is False
