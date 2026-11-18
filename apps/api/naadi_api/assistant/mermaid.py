"""Server-side check of Mermaid source before it reaches the client (render_diagram, flowchart blocks).

Not a full parser: a strict allowlist that rejects what is dangerous or broken and keeps diagrams small.
- One of the supported diagram headers on the first line.
- No init directives (`%%{...}%%` can change securityLevel), no `click`/`href`/`call` interactions, no HTML tags,
  no `javascript:` URLs, no style injection (`style`/`classDef` with url()).
- Balanced brackets and quotes per line; at most 4000 characters, 120 lines and 120 nodes.
"""

from __future__ import annotations

import re

MAX_CHARS = 4000
MAX_LINES = 120
MAX_NODES = 120

_HEADERS = re.compile(
    r"^(?:(?:graph|flowchart)\s+(?:TD|TB|BT|RL|LR)|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram"
    r"|pie(?:\s+title\s+.+)?|mindmap|timeline|journey)\s*;?$"
)
_FORBIDDEN = (
    re.compile(r"%%\s*\{"),
    re.compile(r"^\s*(?:click|call)\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\bhref\b", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>"),
    re.compile(r"url\s*\(", re.IGNORECASE),
)
_PAIRS = {"(": ")", "[": "]", "{": "}"}
_NODE_ID = re.compile(r"(?:^|[\s;>|-])([A-Za-z][\w]*)\s*[\[({]")


class InvalidMermaid(ValueError):
    pass


def _balanced(line: str) -> bool:
    stack: list[str] = []
    in_quote = False
    for ch in line:
        if ch == '"':
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if ch in _PAIRS:
            stack.append(_PAIRS[ch])
        elif ch in _PAIRS.values() and (not stack or stack.pop() != ch):
            return False
    return not stack and not in_quote


def validate_mermaid(source: str) -> str:
    """The normalised source, or InvalidMermaid with a structured reason."""
    src = source.replace("\r\n", "\n").strip()
    if src.startswith("```"):
        src = re.sub(r"^```(?:mermaid)?\s*\n?|\n?```$", "", src).strip()
    if not src:
        raise InvalidMermaid("empty")
    if len(src) > MAX_CHARS:
        raise InvalidMermaid("too_long")
    lines = [ln.rstrip() for ln in src.split("\n")]
    head, sep, rest = lines[0].partition(";")
    if sep and rest.strip():  # "graph LR; A-->B" on one line
        lines = [head.strip(), *(p.strip() for p in rest.split(";") if p.strip()), *lines[1:]]
    if len(lines) > MAX_LINES:
        raise InvalidMermaid("too_many_lines")
    if not _HEADERS.match(lines[0].strip()):
        raise InvalidMermaid("unsupported_diagram")
    for pat in _FORBIDDEN:
        if pat.search(src):
            raise InvalidMermaid("forbidden_syntax")
    is_flow = lines[0].strip().startswith(("graph", "flowchart"))
    nodes: set[str] = set()
    for ln in lines[1:]:
        s = ln.strip()
        if not s or s.startswith("%%"):
            continue
        if is_flow and not _balanced(s):
            raise InvalidMermaid("unbalanced")
        nodes.update(_NODE_ID.findall(" " + s))
    if len(nodes) > MAX_NODES:
        raise InvalidMermaid("too_many_nodes")
    if is_flow and len(lines) < 2:
        raise InvalidMermaid("empty")
    return "\n".join(lines)
