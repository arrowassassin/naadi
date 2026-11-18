"""Pure-Python structure from text: sections by headings (or page ranges), Q&A pairs and multiple-choice questions.

No parsing of untrusted binary formats happens here; this runs on text the sandbox already extracted.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Sequence

from .model import MCQ, QA, Page, Section, Table

SECTION_TARGET_WORDS = 1500
PAGES_PER_SECTION = 8
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_WS = re.compile(r"[ \t]+")


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = "".join(ch for ch in text if ch in "\n\t" or ch.isprintable())
    lines = [_WS.sub(" ", ln).strip() for ln in text.split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if ln:
            blank = 0
            out.append(ln)
        else:
            blank += 1
            if blank == 1:
                out.append("")
    return "\n".join(out).strip()


def short_heading(text: str, limit: int = 120) -> str:
    text = clean_text(text).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _numbered(title: str, n: int) -> str:
    return short_heading(f"{title} · {n}" if title else str(n))


def split_by_words(text: str, title: str, target: int = SECTION_TARGET_WORDS) -> list[Section]:
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    sections: list[Section] = []
    buf: list[str] = []
    words = 0
    for p in paras:
        buf.append(p)
        words += len(p.split())
        if words >= target:
            sections.append(Section(heading=_numbered(title, len(sections) + 1), text="\n\n".join(buf)))
            buf, words = [], 0
    if buf:
        sections.append(
            Section(
                heading=_numbered(title, len(sections) + 1) if sections else short_heading(title), text="\n\n".join(buf)
            )
        )
    return sections


def sections_from_markdown(md: str, title: str) -> list[Section]:
    sections: list[Section] = []
    heading = ""
    level = 1
    buf: list[str] = []

    def flush() -> None:
        body = clean_text("\n".join(buf))
        if body:
            sections.append(Section(heading=short_heading(heading or title), text=body, level=level))

    for line in md.splitlines():
        m = _MD_HEADING.match(line)
        if m and len(m.group(1)) <= 3:
            flush()
            heading, level, buf = m.group(2), len(m.group(1)), []
        else:
            buf.append(line)
    flush()
    if not sections:
        return []
    # One giant headingless section reads badly: split it.
    if len(sections) == 1 and len(sections[0].text.split()) > SECTION_TARGET_WORDS * 2:
        return split_by_words(sections[0].text, title)
    return sections


def sections_from_pages(
    pages: Sequence[Page], title: str, outline: Sequence[tuple[int, str, int]] = ()
) -> list[Section]:
    """Sections from an outline (level, heading, first page) when the PDF has one, else page ranges."""
    texts = {p.number: p.text for p in pages}
    last = max(texts) if texts else 0
    marks = sorted({(page, heading) for level, heading, page in outline if level <= 2 and 1 <= page <= last})
    sections: list[Section] = []
    if len(marks) >= 2:
        if marks[0][0] > 1:
            marks.insert(0, (1, title))
        for i, (start, heading) in enumerate(marks):
            end = marks[i + 1][0] - 1 if i + 1 < len(marks) else last
            if end < start:
                end = start
            body = clean_text("\n\n".join(texts.get(n, "") for n in range(start, end + 1)))
            if body:
                sections.append(Section(heading=short_heading(heading), text=body, page_start=start, page_end=end))
        if sections:
            return sections
    for start in range(1, last + 1, PAGES_PER_SECTION):
        end = min(last, start + PAGES_PER_SECTION - 1)
        body = clean_text("\n\n".join(texts.get(n, "") for n in range(start, end + 1)))
        if body:
            heading = _numbered(title, len(sections) + 1) if last > PAGES_PER_SECTION else short_heading(title)
            sections.append(Section(heading=heading, text=body, page_start=start, page_end=end))
    return sections


# ── tables / CSV ──


def parse_csv(text: str) -> Table:
    sample = text[:8192]
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel_tab if sample.count("\t") > sample.count(",") else csv.excel
    rows: list[list[str]] = []
    for row in csv.reader(io.StringIO(text), dialect):
        cells = [clean_text(c)[:4000] for c in row]
        if any(cells):
            rows.append(cells)
        if len(rows) >= 5000:
            break
    return Table(rows=rows)


_HEADER_FRONT = re.compile(r"^(?:front|question|q|term|prompt|word)$", re.I)
_HEADER_BACK = re.compile(r"^(?:back|answer|a|definition|meaning|response)$", re.I)


def qa_from_table(table: Table) -> list[QA]:
    """Two-column tables (or front/back headers) become Q&A pairs."""
    rows = [r for r in table.rows if len(r) >= 2]
    if len(rows) < 2:
        return []
    front, back = 0, 1
    head = [c.strip() for c in rows[0]]
    fi = next((i for i, c in enumerate(head) if _HEADER_FRONT.match(c)), None)
    bi = next((i for i, c in enumerate(head) if _HEADER_BACK.match(c)), None)
    if fi is not None and bi is not None:
        front, back = fi, bi
        rows = rows[1:]
    elif max(len(r) for r in table.rows) > 3:
        return []
    out = []
    for r in rows:
        if len(r) > max(front, back) and r[front].strip() and r[back].strip():
            out.append(QA(front=r[front].strip()[:4000], back=r[back].strip()[:8000]))
    return out if len(out) >= 2 else []


_QA_LINE = re.compile(r"^\s*(?:Q(?:uestion)?|Front)\s*[:.)\-]\s*(.+)$", re.I)
_A_LINE = re.compile(r"^\s*(?:A(?:nswer|ns)?|Back)\s*[:.)\-]\s*(.+)$", re.I)


def qa_from_text(text: str) -> list[QA]:
    out: list[QA] = []
    q: str | None = None
    for line in text.splitlines():
        mq = _QA_LINE.match(line)
        if mq:
            q = mq.group(1).strip()
            continue
        ma = _A_LINE.match(line)
        if ma and q:
            out.append(QA(front=q[:4000], back=ma.group(1).strip()[:8000]))
            q = None
    return out if len(out) >= 2 else []


# ── MCQs ──
_STEM = re.compile(r"^\s*(?:Q(?:uestion)?\s*)?(\d{1,4})[.):]\s+(.+)$", re.I)
_OPTION = re.compile(r"^\s*\(?([A-Ja-j])[.)]\s+(.+)$")
_ANSWER = re.compile(r"^\s*(?:correct\s+)?(?:answer|ans|key)\s*[:\-]?\s*\(?([A-Ja-j])\)?\b", re.I)
_EXPL = re.compile(r"^\s*(?:explanation|rationale|why)\s*[:\-]\s*(.*)$", re.I)


def mcqs_from_text(text: str) -> list[MCQ]:
    """Numbered stems, lettered options and an `Answer: X` line (the student's own question sets)."""
    out: list[MCQ] = []
    stem: list[str] = []
    options: list[str] = []
    answer: str | None = None
    expl: list[str] = []
    mode = "none"

    def flush() -> None:
        if stem and len(options) >= 2 and answer is not None and ord(answer) - 65 < len(options):
            out.append(
                MCQ(
                    stem=clean_text(" ".join(stem))[:8000],
                    options=[o[:2000] for o in options[:10]],
                    answer=answer,
                    explanation=clean_text(" ".join(expl))[:8000],
                )
            )

    for line in text.splitlines():
        if not line.strip():
            continue
        ms = _STEM.match(line)
        mo = _OPTION.match(line)
        ma = _ANSWER.match(line)
        me = _EXPL.match(line)
        if ms and not mo and mode in ("none", "answer", "expl"):
            flush()
            stem, options, answer, expl, mode = [ms.group(2)], [], None, [], "stem"
        elif mo and mode in ("stem", "options") and ord(mo.group(1).upper()) - 65 == len(options):
            options.append(mo.group(2).strip())
            mode = "options"
        elif ma and mode == "options":
            answer = ma.group(1).upper()
            mode = "answer"
        elif me and mode in ("answer",):
            expl = [me.group(1)]
            mode = "expl"
        elif mode == "stem":
            stem.append(line.strip())
        elif mode == "expl":
            expl.append(line.strip())
    flush()
    return out if len(out) >= 2 else []


def strip_rtf(rtf: str) -> str:
    """Plain text of an RTF document (control words and groups such as fonts/colours/pictures dropped)."""
    out: list[str] = []
    stack: list[bool] = []
    ignore = False
    i, n = 0, len(rtf)
    skip_groups = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header", "footer", "*"}
    while i < n:
        ch = rtf[i]
        if ch == "{":
            stack.append(ignore)
            i += 1
        elif ch == "}":
            ignore = stack.pop() if stack else False
            i += 1
        elif ch == "\\":
            i += 1
            if i >= n:
                break
            c = rtf[i]
            if c in "\\{}":
                if not ignore:
                    out.append(c)
                i += 1
            elif c == "'":
                hexv = rtf[i + 1 : i + 3]
                if not ignore and re.fullmatch(r"[0-9a-fA-F]{2}", hexv):
                    out.append(bytes.fromhex(hexv).decode("cp1252", errors="replace"))
                i += 3
            elif c == "*":
                ignore = True
                i += 1
            elif c.isalpha():
                m = re.match(r"([a-zA-Z]{1,32})(-?\d{1,10})? ?", rtf[i:])
                assert m is not None
                word = m.group(1)
                i += m.end()
                if word in skip_groups:
                    ignore = True
                elif not ignore and word in ("par", "line", "sect", "page"):
                    out.append("\n")
                elif not ignore and word == "tab":
                    out.append("\t")
                elif word == "u" and m.group(2) and not ignore:
                    code = int(m.group(2))
                    out.append(chr(code + 65536 if code < 0 else code))
                    if i < n and rtf[i] == "?":
                        i += 1
            else:
                i += 1
        else:
            if not ignore and ch not in "\r\n":
                out.append(ch)
            i += 1
    return clean_text("".join(out))


def first_heading(sections: Iterable[Section]) -> str:
    for s in sections:
        if s.heading:
            return s.heading
    return ""


__all__ = [
    "clean_text",
    "mcqs_from_text",
    "parse_csv",
    "qa_from_table",
    "qa_from_text",
    "sections_from_markdown",
    "sections_from_pages",
    "short_heading",
    "split_by_words",
    "strip_rtf",
]
