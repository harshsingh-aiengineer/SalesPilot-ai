import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def db_url(tmp_path, monkeypatch):
    path = (tmp_path / "salespilot.db").as_posix()
    url = f"sqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SALESPILOT_PASSWORD", "test-password")
    monkeypatch.setenv("SALESPILOT_SECRET", "test-secret-value")
    monkeypatch.setenv("SALESPILOT_AUTH_REQUIRED", "true")
    monkeypatch.setenv("SALESPILOT_DEV_MODE", "false")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")
    from app.config import reset_settings_cache

    reset_settings_cache()
    from app.database import configure_engine

    configure_engine(url)
    return url


@pytest.fixture()
def client(db_url):
    from app.main import create_app

    application = create_app()
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture()
def auth_header(client):
    response = client.post("/api/v1/auth/login", json={"password": "test-password"})
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}
