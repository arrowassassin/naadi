"""Privacy guards applied to every model call (`naadi_api.llm` runs both before any provider sees a byte).

1. **Vault content can never reach a model.** Vault documents are read only as `VaultDocument`, which is not
   a `str`, has no `__str__` that reveals its bytes and cannot be placed in a message by the type checker.
   At runtime `assert_no_vault` rejects anything that still carries vault provenance: a `VaultDocument`
   object anywhere in a message, a content part or message flagged `naadi_source = "vault"`, or a URL into
   the private `vault` Storage bucket.
2. **PII is replaced with placeholders.** Free model endpoints (NVIDIA, several OpenRouter providers) log
   prompts. Before every call the student's name, email, phone numbers, long numeric / document IDs,
   UUIDs and any string from their `profiles` row are replaced by `[name]`, `[email]`, `[phone]`, `[id]`
   or `[profile]`. The replacement is deterministic and one-way; nothing maps placeholders back.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, final

from langchain_core.messages import AIMessage, BaseMessage

VAULT_MARK: Final = "vault"
_VAULT_URL = re.compile(r"/storage/v1/object/(?:public/|sign/|authenticated/)?vault/", re.IGNORECASE)

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Phone numbers: an optional +country code, then at least 10 digits in total with common separators. Lab
# values ("pH 7.21", "120/80", "HCO3 24") never reach 10 digits, so clinical text is left alone.
_PHONE = re.compile(r"(?<![\w.])(?:\+\d{1,3}[\s\-.]?)?(?:\(?\d{2,5}\)?[\s\-.]?){2,5}\d{2,5}(?![\w.])")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
# Document-style IDs: passport / licence / registration numbers (letters + 6+ digits) and bare 8+ digit runs.
_DOC_ID = re.compile(r"\b(?:[A-Z]{1,3}\d{6,12}|\d{8,})\b")
# Public literature / trial identifiers are citations, not personal data: keep them.
_CITATION_ID = re.compile(r"^(?:NCT|PMC)\d+$")
_CITATION_PREFIX = re.compile(r"(?:PMID|PubMed|doi|DOI|ISBN|ISSN)[:\s#]*$")
_DIGIT = re.compile(r"\d")
MIN_NAME_PART = 3


class VaultContentRejected(PermissionError):
    """Raised when vault material is about to reach a model. Never caught by the fallback walker."""


@final
class VaultDocument:
    """A vault file's bytes in memory. Deliberately not a `str`/`bytes` subclass: it cannot be put into a
    prompt by accident, its repr hides the content, and `assert_no_vault` rejects it if it is forced in."""

    __slots__ = ("_data", "storage_path")

    def __init__(self, data: bytes, storage_path: str) -> None:
        self._data = data
        self.storage_path = storage_path

    @property
    def data(self) -> bytes:
        return self._data

    def __repr__(self) -> str:
        return f"VaultDocument(path={self.storage_path!r}, bytes={len(self._data)})"

    __str__ = __repr__


def _vault_in(value: Any, depth: int = 0) -> bool:
    if depth > 12:
        return False
    if isinstance(value, VaultDocument):
        return True
    if isinstance(value, str):
        return bool(_VAULT_URL.search(value))
    if isinstance(value, Mapping):
        if value.get("naadi_source") == VAULT_MARK:
            return True
        return any(_vault_in(v, depth + 1) for v in value.values())
    if isinstance(value, list | tuple):
        return any(_vault_in(v, depth + 1) for v in value)
    return False


def assert_no_vault(messages: Sequence[BaseMessage]) -> None:
    for m in messages:
        if (
            _vault_in(m.content)
            or m.additional_kwargs.get("naadi_source") == VAULT_MARK
            or _vault_in(m.additional_kwargs)
            or (isinstance(m, AIMessage) and _vault_in([tc.get("args") for tc in m.tool_calls]))
        ):
            raise VaultContentRejected("vault content can never be sent to a model")


@dataclass(frozen=True)
class PrivacyContext:
    """What identifies this student. Built from `auth.users` + `profiles` (never logged, never sent)."""

    names: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()
    profile_values: tuple[str, ...] = ()
    _patterns: tuple[tuple[re.Pattern[str], str], ...] = field(default=(), init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        pats: list[tuple[re.Pattern[str], str]] = []
        for email in self.emails:
            if email:
                pats.append((re.compile(re.escape(email), re.IGNORECASE), "[email]"))
                local = email.split("@", 1)[0]
                if len(local) >= 4:
                    pats.append((re.compile(rf"\b{re.escape(local)}\b", re.IGNORECASE), "[name]"))
        for phone in self.phones:
            digits = "".join(_DIGIT.findall(phone))
            if len(digits) >= 6:
                # Any formatting of the same digits: 98765 43210, +91-98765-43210, ...
                pats.append((re.compile(r"[\s\-.()]*".join(re.escape(d) for d in digits[-10:])), "[phone]"))
        whole = sorted({n.strip() for n in self.names if n and n.strip()}, key=len, reverse=True)
        parts = sorted(
            {p for n in whole for p in re.split(r"[\s\-.,']+", n) if len(p) >= MIN_NAME_PART},
            key=len,
            reverse=True,
        )
        for n in [*whole, *parts]:
            pats.append((re.compile(rf"(?<!\w){re.escape(n)}(?!\w)", re.IGNORECASE), "[name]"))
        for v in sorted({v.strip() for v in self.profile_values if v and len(v.strip()) >= 4}, key=len, reverse=True):
            pats.append((re.compile(rf"(?<!\w){re.escape(v)}(?!\w)", re.IGNORECASE), "[profile]"))
        object.__setattr__(self, "_patterns", tuple(pats))

    @classmethod
    def from_profile(
        cls,
        *,
        display_name: str | None,
        email: str | None,
        phone: str | None = None,
        full_name: str | None = None,
        settings: Mapping[str, Any] | None = None,
    ) -> PrivacyContext:
        return cls(
            names=tuple(n for n in (display_name, full_name) if n),
            emails=(email,) if email else (),
            phones=(phone,) if phone else (),
            profile_values=tuple(_strings(settings or {})),
        )

    def scrub(self, text: str) -> str:
        for pat, repl in self._patterns:
            text = pat.sub(repl, text)
        return scrub_generic(text)


def _strings(value: Any, depth: int = 0) -> Iterable[str]:
    if depth > 6:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _strings(v, depth + 1)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _strings(v, depth + 1)


def _phone_sub(m: re.Match[str]) -> str:
    return "[phone]" if len(_DIGIT.findall(m.group(0))) >= 10 else m.group(0)


def scrub_generic(text: str) -> str:
    """Patterns that are PII for anyone: emails, phone numbers, UUIDs, document-style IDs."""
    text = _EMAIL.sub("[email]", text)
    text = _UUID.sub("[id]", text)
    text = _PHONE.sub(_phone_sub, text)
    return _DOC_ID.sub(lambda m: _doc_id_sub(m, text), text)


def _doc_id_sub(m: re.Match[str], text: str) -> str:
    token = m.group(0)
    if _CITATION_ID.match(token) or _CITATION_PREFIX.search(text[max(0, m.start() - 12) : m.start()]):
        return token
    return "[id]"


def _scrub_value(value: Any, ctx: PrivacyContext, depth: int = 0) -> Any:
    if depth > 12:
        return value
    if isinstance(value, str):
        return ctx.scrub(value)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            # Image/file payloads (base64 data URLs, file data) are not text: leave the bytes alone.
            if k in {"image_url", "url", "file_data", "data", "base64"} and isinstance(v, str | Mapping):
                out[k] = v
            else:
                out[k] = _scrub_value(v, ctx, depth + 1)
        return out
    if isinstance(value, list):
        return [_scrub_value(v, ctx, depth + 1) for v in value]
    return value


def scrub_messages(messages: Sequence[BaseMessage], ctx: PrivacyContext) -> list[BaseMessage]:
    """Copies of `messages` with PII replaced in every text field a provider receives."""
    out: list[BaseMessage] = []
    for m in messages:
        update: dict[str, Any] = {"content": _scrub_value(m.content, ctx)}
        if isinstance(m, AIMessage) and m.tool_calls:
            update["tool_calls"] = [{**tc, "args": _scrub_value(tc.get("args") or {}, ctx)} for tc in m.tool_calls]
        out.append(m.model_copy(update=update))
    return out


__all__ = [
    "VAULT_MARK",
    "PrivacyContext",
    "VaultContentRejected",
    "VaultDocument",
    "assert_no_vault",
    "scrub_generic",
    "scrub_messages",
]
