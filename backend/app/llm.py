"""Ollama HTTP client with a cross-process lock so only one heavy request runs."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ResourceLock
from app.timeutils import utcnow


class LlmError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class LlmLock:
    def __init__(self, db: Session, holder: str | None = None, ttl_seconds: int | None = None):
        self.db = db
        self.holder = holder or f"llm-{uuid.uuid4().hex[:12]}"
        self.ttl_seconds = ttl_seconds or (get_settings().ollama_timeout_seconds + 30)
        self.acquired = False

    def __enter__(self) -> "LlmLock":
        now = utcnow()
        expires = now + timedelta(seconds=self.ttl_seconds)
        lock = self.db.get(ResourceLock, "llm")
        if lock is None:
            lock = ResourceLock(name="llm", holder=None, expires_at=None)
            self.db.add(lock)
            self.db.flush()
        if lock.holder and lock.holder != self.holder and lock.expires_at and lock.expires_at > now:
            raise LlmError(
                "llm_busy",
                "The local model is already handling another request. Wait for it to finish, then try again.",
            )
        lock.holder = self.holder
        lock.expires_at = expires
        self.db.commit()
        self.acquired = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if not self.acquired:
            return
        lock = self.db.get(ResourceLock, "llm")
        if lock and lock.holder == self.holder:
            lock.holder = None
            lock.expires_at = None
            self.db.commit()


def _timeout() -> httpx.Timeout:
    seconds = get_settings().ollama_timeout_seconds
    return httpx.Timeout(seconds, connect=5.0)


def ollama_tags() -> dict:
    base = get_settings().ollama_base_url.rstrip("/")
    try:
        response = httpx.get(f"{base}/api/tags", timeout=httpx.Timeout(20.0, connect=5.0))
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise LlmError("ollama_unavailable", f"Ollama is not reachable at {base}: {exc.__class__.__name__}") from exc


def model_ready() -> dict:
    settings = get_settings()
    try:
        payload = ollama_tags()
    except LlmError as exc:
        return {
            "reachable": False,
            "model": settings.ollama_model,
            "available": False,
            "error": exc.message,
            "code": exc.code,
        }
    names = [item.get("name", "") for item in payload.get("models", [])]
    ready = _model_is_installed(names, settings.ollama_model)
    return {
        "reachable": True,
        "model": settings.ollama_model,
        "available": ready,
        "installed_models": names,
        "error": None if ready else f"Model {settings.ollama_model} is not installed in Ollama.",
        "code": None if ready else "model_missing",
    }


def _model_is_installed(names: list[str], wanted: str) -> bool:
    wanted = wanted.strip()
    candidates = {wanted, f"{wanted}:latest"}
    if wanted.endswith(":latest"):
        candidates.add(wanted[: -len(":latest")])
    return any(name in candidates for name in names)


def chat(messages: list[dict], tools: list[dict] | None = None, *, force_json: bool = False) -> dict:
    settings = get_settings()
    body: dict = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.1,
            "num_ctx": settings.ollama_num_ctx,
            "num_predict": settings.ollama_num_predict,
        },
    }
    if tools:
        body["tools"] = tools
    if force_json:
        body["format"] = "json"
    base = settings.ollama_base_url.rstrip("/")
    try:
        response = httpx.post(f"{base}/api/chat", json=body, timeout=_timeout())
    except httpx.TimeoutException as exc:
        raise LlmError("ollama_timeout", "The local model did not respond before the timeout.") from exc
    except httpx.HTTPError as exc:
        raise LlmError("ollama_unavailable", f"Ollama request failed: {exc.__class__.__name__}") from exc
    if response.status_code == 400 and "think" in body:
        body.pop("think", None)
        try:
            response = httpx.post(f"{base}/api/chat", json=body, timeout=_timeout())
        except httpx.HTTPError as exc:
            raise LlmError("ollama_unavailable", f"Ollama request failed: {exc.__class__.__name__}") from exc
    if response.status_code >= 400:
        detail = response.text[:300]
        raise LlmError("ollama_error", f"Ollama returned HTTP {response.status_code}: {detail}")
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise LlmError("ollama_error", "Ollama returned a response that was not valid JSON.") from exc


def probe_model() -> dict:
    """Readiness probe: model must be installed and answer a tiny request."""
    status = model_ready()
    if not status["available"]:
        return {"ok": False, **status, "reply": None}
    try:
        payload = chat(
            [{"role": "user", "content": "Reply with the single word ready."}],
            tools=None,
        )
    except LlmError as exc:
        return {"ok": False, **status, "available": True, "error": exc.message, "code": exc.code, "reply": None}
    message = (payload.get("message") or {}).get("content") or ""
    return {"ok": True, **status, "error": None, "code": None, "reply": message.strip()[:200]}
