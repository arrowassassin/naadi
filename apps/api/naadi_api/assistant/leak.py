"""Last-line checks on everything a tool hands back (segment 14, safety rule: never leak an answer).

The real guarantee is structural: tools read questions through `questions.question_for_assistant`, which returns
the hidden fields only after a revealed attempt. This module catches a mistake in a new tool before it ships:

- no block, citation or canvas payload ever carries a key named like a hidden question field (a revealed
  explanation reaches the model as text in `content`, never as structured data the client could cache);
- no vault material (VaultDocument, vault storage URLs, vault-marked dicts);
- e-mail addresses in tool output are replaced with a placeholder (`scrub_payload`) before anything is shown.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from ..llm_privacy import VAULT_MARK, VaultDocument
from .questions import HIDDEN_FIELDS

_VAULT_URL = re.compile(r"/storage/v1/object/(?:public/|sign/|authenticated/)?vault/", re.IGNORECASE)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


class LeakBlocked(PermissionError):
    """A tool tried to hand over something that must never leave the server. The run reports a tool error."""


def _walk(value: Any, path: str, depth: int = 0) -> Iterable[tuple[str, Any]]:
    if depth > 14:
        return
    yield path, value
    if isinstance(value, Mapping):
        for k, v in value.items():
            yield from _walk(v, f"{path}.{k}", depth + 1)
    elif isinstance(value, list | tuple):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]", depth + 1)


def check_payload(value: Any, *, where: str, allow_hidden: bool = False) -> None:
    for path, v in _walk(value, where):
        if isinstance(v, VaultDocument):
            raise LeakBlocked(f"vault document in {path}")
        if isinstance(v, Mapping):
            if v.get("naadi_source") == VAULT_MARK:
                raise LeakBlocked(f"vault-marked value in {path}")
            bad = [] if allow_hidden else [k for k in v if k in HIDDEN_FIELDS]
            if bad:
                raise LeakBlocked(f"hidden question field {bad[0]!r} in {path}")
        if isinstance(v, str) and _VAULT_URL.search(v):
            raise LeakBlocked(f"vault URL in {path}")


def check_text(text: str, *, where: str) -> None:
    check_payload(text, where=where)


def scrub_payload(value: Any, depth: int = 0) -> Any:
    """A copy of `value` with every e-mail address replaced by "[email]"."""
    if depth > 14:
        return value
    if isinstance(value, str):
        return _EMAIL.sub("[email]", value)
    if isinstance(value, Mapping):
        return {k: scrub_payload(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [scrub_payload(v, depth + 1) for v in value]
    return value
