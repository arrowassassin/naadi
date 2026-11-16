"""Remove secret material from strings before they reach a response or a log line."""

from __future__ import annotations

import re
from collections.abc import Iterable

_PG_PASSWORD = re.compile(r"(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@")
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=\-]+", re.IGNORECASE)
_KEYLIKE = re.compile(r"\b(sk-or-v1-|tvly-|hf_|sbp_|nvapi-|eyJ)[A-Za-z0-9._\-]{8,}")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Mask known secret values and secret-shaped substrings; keeps length and line breaks otherwise."""
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, "***")
    text = _PG_PASSWORD.sub(r"\1***@", text)
    text = _BEARER.sub(r"\1***", text)
    text = _KEYLIKE.sub(r"\1***", text)
    return _EMAIL.sub("***@***", text)


def scrub(text: str, secrets: Iterable[str] = (), max_len: int = 240) -> str:
    """`redact`, then collapse whitespace and cap the length (for one-line details shown to users)."""
    text = " ".join(redact(text, secrets).split())
    return text if len(text) <= max_len else text[: max_len - 1] + "…"
