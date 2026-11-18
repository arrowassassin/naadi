"""Pulling one JSON object out of a model reply."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


class NoJson(ValueError):
    pass


def first_object(text: str) -> dict[str, Any]:
    """The first balanced `{...}` of `text` (a reply may wrap it in a code fence or add a sentence), parsed."""
    s = _FENCE.sub("", text.strip())
    start = s.find("{")
    if start < 0:
        raise NoJson("no object")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(s[start : i + 1])
                except json.JSONDecodeError as exc:
                    raise NoJson("invalid json") from exc
                if not isinstance(obj, dict):
                    raise NoJson("not an object")
                return obj
    raise NoJson("unterminated object")


def text_of(content: Any) -> str:
    """A LangChain message's content as one string (a list of parts joins its text parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p if isinstance(p, str) else str(p.get("text", "")) for p in content if p)
    return str(content)
