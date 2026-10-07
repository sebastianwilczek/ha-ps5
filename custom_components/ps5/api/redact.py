"""Redaction helpers for anything that may end up in logs."""

from __future__ import annotations

import re
from typing import Any

REDACTED = "**REDACTED**"

# CryptoJS ciphertexts (console duids) and our client duid.
_CIPHERTEXT_RE = re.compile(r"U2FsdGVkX1[A-Za-z0-9+/=]*")
_CLIENT_DUID_RE = re.compile(r"0000000700090100[0-9a-f]{64}")
_SENSITIVE_KEYS = {
    "duid",
    "npsso",
    "access_token",
    "refresh_token",
    "id_token",
    "authorization",
    "account_id",
    "accountid",
}


def redact_text(text: str) -> str:
    """Remove duids from free text."""
    return _CLIENT_DUID_RE.sub(REDACTED, _CIPHERTEXT_RE.sub(REDACTED, text))


def redact_obj(obj: Any) -> Any:
    """Return a deep copy of a JSON-like value with secrets removed."""
    if isinstance(obj, dict):
        return {
            key: REDACTED
            if isinstance(key, str) and key.lower() in _SENSITIVE_KEYS
            else redact_obj(value)
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [redact_obj(value) for value in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj
