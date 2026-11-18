#!/usr/bin/env python3
"""Generate flashcards from each content pack's own text (segment 23, S23-0). Deterministic; no model call.

    python tools/generate_cards.py            # rewrite data/content/<pack>/cards.generated.json for every pack
    python tools/generate_cards.py --check    # exit 1 when a committed file is stale (CI drift guard)

Two kinds of card, both derived only from Naadi-original pack text (so they are original too):
  * key-fact cloze cards: every <span class="hy">…</span> in a section's html is a key fact (the reader shows it
    as a key-fact highlight). The card's front is the
    sentence with the span blanked ("[…]"); the back is the sentence with the span in bold. One card per span.
  * objective cards: every published item with an objective. The front is the objective; the back is the item's
    "Takeaway:" line (else the first sentence of its explanation). The card carries `questionId`, so it becomes
    visible to a student only after they have attempted that item (it restates the answer; flashcards RLS).

The importer loads cards.generated.json next to the authored cards.json; both are shared seed cards (origin
`seed`), which every student's FSRS queue picks up as new cards (naadi_api/cards). Ids are
`<idPrefix>-g-<hash of the source>`, stable while the section or item id is. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "data" / "content"
OUT = "cards.generated.json"
SOURCE = "naadi-original-v1"

_BLOCK = re.compile(r"<(p|li|td|th)\b[^>]*>(.*?)</\1>", re.S | re.I)
_MARK = re.compile(r"<span\s+class=\"hy\"\s*>(.*?)</span>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])")
_TAKEAWAY = re.compile(r"Takeaway:?\s*</strong>\s*(.*?)(?:</p>|$)|Takeaway:\s*(.*?)(?:</p>|$)", re.S | re.I)


def text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", s))).strip()


def _id(prefix: str, *parts: str) -> str:
    return f"{prefix}-g-{hashlib.sha1('|'.join(parts).encode()).hexdigest()[:12]}"  # noqa: S324 - an id, not security


def cloze_cards(prefix: str, chapter: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sec in chapter.get("sections", []):
        n = 0
        for block in _BLOCK.finditer(sec.get("html", "")):
            inner = block.group(2)
            # Work sentence by sentence on the marked-up text, so the card holds one fact, not a whole paragraph.
            for sentence in _SENTENCE.split(inner):
                for m in _MARK.finditer(sentence):
                    fact = text(m.group(1))
                    if not fact:
                        continue
                    before, after = sentence[: m.start()], sentence[m.end() :]
                    front = text(f"{before}\x00{after}").replace("\x00", "[…]")
                    bold = f"<strong>{html.escape(fact)}</strong>"
                    back = html.escape(text(f"{before}\x00{after}")).replace("\x00", bold)
                    out.append(
                        {
                            "id": _id(prefix, sec["id"], "mark", str(n)),
                            "frontHtml": f"<p>{html.escape(sec['heading'])}: {html.escape(front)}</p>",
                            "backHtml": f"<p>{back}</p>",
                            "sectionId": sec["id"],
                            "questionId": None,
                            "areas": list(sec.get("areas", [])),
                            "source": SOURCE,
                            "tags": ["generated", "key-fact"],
                        }
                    )
                    n += 1
    return out


def objective_card(prefix: str, item: dict[str, Any]) -> dict[str, Any] | None:
    objective = text(item.get("objective") or "")
    if not objective or item.get("status", "published") != "published":
        return None
    explanation = item.get("explanationHtml") or ""
    m = _TAKEAWAY.search(explanation)
    back = text((m.group(1) or m.group(2)) if m else "") or (_SENTENCE.split(text(explanation)) or [""])[0]
    if not back:
        return None
    return {
        "id": _id(prefix, item["id"], "objective"),
        "frontHtml": f"<p>{html.escape(objective)}</p>",
        "backHtml": f"<p>{html.escape(back)}</p>",
        "sectionId": None,
        "questionId": item["id"],
        "areas": list(item.get("areas", [])),
        "source": SOURCE,
        "tags": ["generated", "objective"],
    }


def build(pack_dir: Path) -> str:
    meta = json.loads((pack_dir / "pack.json").read_text(encoding="utf-8"))
    prefix = meta["idPrefix"]
    cards: list[dict[str, Any]] = []
    chapters = pack_dir / "chapters.json"
    if chapters.is_file():
        for ch in json.loads(chapters.read_text(encoding="utf-8")):
            if ch.get("source", SOURCE) == SOURCE:
                cards += cloze_cards(prefix, ch)
    questions = pack_dir / "questions.json"
    if questions.is_file():
        for item in json.loads(questions.read_text(encoding="utf-8")).get("items", []):
            if item.get("source", SOURCE) == SOURCE and (card := objective_card(prefix, item)):
                cards.append(card)
    return json.dumps(cards, indent=1, ensure_ascii=False) + "\n"


def pack_dirs(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if (p / "pack.json").is_file()) if root.is_dir() else []


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true")
    p.add_argument("--root", type=Path, action="append", help="pack root(s); default data/content")
    args = p.parse_args(argv)
    stale = []
    total = 0
    for root in args.root or [CONTENT]:
        for d in pack_dirs(root):
            fresh = build(d)
            total += fresh.count('"id":')
            path = d / OUT
            if args.check:
                if not path.is_file() or path.read_text(encoding="utf-8") != fresh:
                    stale.append(str(path.relative_to(ROOT)))
            else:
                path.write_text(fresh, encoding="utf-8")
    if stale:
        print(f"stale: {', '.join(stale)}; run `python tools/generate_cards.py` and commit", file=sys.stderr)
        return 1
    print(f"{total} generated cards", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
