from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

SECRET_KEY_MARKERS = (
    "token",
    "password",
    "passwd",
    "pwd",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "bearer",
)

SECRET_TEXT_MARKERS = (
    "token=",
    "password=",
    "passwd=",
    "pwd=",
    "secret=",
    "api_key=",
    "apikey=",
    "authorization:",
    "bearer ",
)


def _looks_secret_key(key: object) -> bool:
    text = str(key).lower().replace("-", "_")
    return any(marker in text for marker in SECRET_KEY_MARKERS)


def _redact_text(value: object) -> str:
    text = str(value)
    lowered = text.lower()
    if any(marker in lowered for marker in SECRET_TEXT_MARKERS):
        return "[REDACTED]"
    if _url_contains_credentials(text):
        return "[REDACTED]"
    return text


def _url_contains_credentials(text: str) -> bool:
    try:
        parsed = urlsplit(text)
    except ValueError:
        return False
    return bool(parsed.scheme and parsed.netloc and (parsed.username or parsed.password))


def _sanitize_value(value: object) -> object:
    if isinstance(value, Mapping):
        sanitized: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            sanitized[key_text] = "[REDACTED]" if _looks_secret_key(key_text) else _sanitize_value(item)
        return sanitized
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [_sanitize_value(item) for item in value]
    return value
