"""Redact secrets before they reach logs, audit records, or API responses."""

from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEYS = {
    "password",
    "token",
    "access_token",
    "refresh_token",
    "client_secret",
    "authorization",
    "secret",
    "id_token",
    "salespilot_secret",
    "salespilot_password",
}

_SECRET_PATTERNS = [
    re.compile(r"ya29\.[0-9A-Za-z\-_]+"),
    re.compile(r"1//[0-9A-Za-z\-_]+"),
]


def redact(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[truncated]"
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                cleaned[key] = "[redacted]"
            else:
                cleaned[key] = redact(item, depth + 1)
        return cleaned
    if isinstance(value, list):
        return [redact(item, depth + 1) for item in value[:50]]
    if isinstance(value, str):
        text = value
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub("[redacted]", text)
        if len(text) > 2000:
            return text[:2000] + "…"
        return text
    return value
