"""Decode Gmail API message payloads. Email content is untrusted input."""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from html.parser import HTMLParser


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):  # type: ignore[no-untyped-def]
        if tag in {"script", "style"}:
            self._skip = True
        if tag in {"p", "br", "div", "tr", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):  # type: ignore[no-untyped-def]
        if tag in {"script", "style"}:
            self._skip = False

    def handle_data(self, data):  # type: ignore[no-untyped-def]
        if not self._skip:
            self.parts.append(data)


def strip_html(value: str) -> str:
    parser = _HTMLText()
    parser.feed(value)
    text = "".join(parser.parts)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def decode_b64(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("utf-8"))
    return raw.decode("utf-8", errors="replace")


def _walk(payload: dict, texts: list[str], htmls: list[str]) -> None:
    mime = (payload.get("mimeType") or "").lower()
    body = (payload.get("body") or {}).get("data")
    if body and mime == "text/plain":
        texts.append(decode_b64(body))
    elif body and mime == "text/html":
        htmls.append(decode_b64(body))
    for part in payload.get("parts") or []:
        _walk(part, texts, htmls)


def extract_body(payload: dict | None, limit: int = 50_000) -> str:
    if not payload:
        return ""
    texts: list[str] = []
    htmls: list[str] = []
    _walk(payload, texts, htmls)
    if texts:
        body = "\n".join(part.strip() for part in texts if part.strip())
    elif htmls:
        body = strip_html("\n".join(htmls))
    else:
        data = (payload.get("body") or {}).get("data")
        body = decode_b64(data) if data else ""
    body = body.replace("\x00", "").strip()
    if len(body) > limit:
        return body[:limit] + "\n[truncated]"
    return body


def header_map(payload: dict | None) -> dict[str, str]:
    headers = {}
    for item in (payload or {}).get("headers") or []:
        name = str(item.get("name") or "").lower()
        if name and name not in headers:
            headers[name] = str(item.get("value") or "")
    return headers


def parse_gmail_message(message: dict) -> dict:
    payload = message.get("payload") or {}
    headers = header_map(payload)
    internal = message.get("internalDate")
    received_at = None
    if internal:
        try:
            received_at = datetime.fromtimestamp(int(internal) / 1000, tz=timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError, OSError):
            received_at = None
    labels = message.get("labelIds") or []
    direction = "outbound" if "SENT" in labels else "inbound"
    return {
        "provider": "gmail",
        "provider_message_id": message.get("id") or "",
        "thread_id": message.get("threadId"),
        "direction": direction,
        "sender": headers.get("from"),
        "recipients": headers.get("to"),
        "subject": headers.get("subject"),
        "snippet": (message.get("snippet") or "")[:500],
        "body_text": extract_body(payload),
        "labels": labels,
        "received_at": received_at,
        "message_id_header": headers.get("message-id"),
        "list_unsubscribe": headers.get("list-unsubscribe"),
        "precedence": headers.get("precedence"),
        "auto_submitted": headers.get("auto-submitted"),
    }


_NOISE = re.compile(
    r"\b(noreply|no-reply|notifications?|newsletter|unsubscribe|mailer-daemon|donotreply)\b",
    re.I,
)


def looks_like_noise(parsed: dict) -> bool:
    """Cheap filter so newsletters and automated mail are not treated as leads."""
    if parsed.get("direction") == "outbound":
        return True
    if parsed.get("list_unsubscribe"):
        return True
    precedence = (parsed.get("precedence") or "").lower()
    if precedence in {"bulk", "list", "junk"}:
        return True
    auto = (parsed.get("auto_submitted") or "").lower()
    if auto and auto != "no":
        return True
    sender = parsed.get("sender") or ""
    subject = parsed.get("subject") or ""
    if _NOISE.search(sender) or _NOISE.search(subject):
        return True
    labels = set(parsed.get("labels") or [])
    if labels & {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "SPAM", "TRASH"}:
        return True
    return False


def extract_email_address(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})", value, re.I)
    return match.group(1).lower() if match else None


def extract_display_name(value: str | None) -> str | None:
    if not value:
        return None
    if "<" in value:
        name = value.split("<", 1)[0].strip().strip('"')
        return name or None
    if "@" in value:
        return None
    return value.strip() or None
