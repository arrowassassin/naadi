"""The sensitivity gate: local rules that find identity and personal documents before any model call.

Runs on text the pipeline extracted locally (PyMuPDF text, markitdown, local OCR of images and scanned pages). It
never calls a model, never logs the text, and reports only signal codes and counts.

Strong signals (one is enough):
- passport / ID machine-readable zone (ICAO 9303 TD1/TD2/TD3) with valid check digits;
- Aadhaar number (12 digits, first 2-9) that passes the Verhoeff check;
- PAN (`AAAAA9999A` with a valid holder-type letter) next to an income-tax / PAN label;
- US Social Security number in `AAA-GG-SSSS` form with valid ranges next to an SSN label;
- SEVIS id (`N` + 10 digits) or a US visa control/foil number next to a visa / I-20 / DS-2019 label.

Document signals (a document keyword AND a personal-details label such as "Surname", "Given names", "Date of
birth", "Name of the candidate", "Received from"): passport/ID card, diploma or degree certificate, visa, I-20 /
DS-2019, bank or fee receipt. Medical notes that merely mention "visa" or "receipt" do not trigger: the personal
label is required.

`redactions` are the exact identifier strings found; when the student says the file is not personal, they are
replaced by `[id]` in everything derived from it (a second rules pass) before any model call or storage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import pairwise

# ── Verhoeff (Aadhaar) ──
_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def verhoeff_valid(digits: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_digit(digits: str) -> str:
    """The check digit to append to `digits` (used to build synthetic test numbers)."""
    inv = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(inv[c])


# ── ICAO 9303 MRZ ──
_MRZ_W = (7, 3, 1)


def _mrz_value(ch: str) -> int:
    if ch.isdigit():
        return int(ch)
    if "A" <= ch <= "Z":
        return ord(ch) - 55
    return 0  # '<'


def mrz_check(field: str) -> str:
    return str(sum(_mrz_value(c) * _MRZ_W[i % 3] for i, c in enumerate(field)) % 10)


def _ok(field: str, digit: str) -> bool:
    return digit.isdigit() and mrz_check(field) == digit


_MRZ_CHARS = re.compile(r"[A-Z0-9<]+")


def _mrz_lines(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        line = raw.strip().replace(" ", "").replace("«", "<").upper()
        if len(line) >= 30 and _MRZ_CHARS.fullmatch(line) and line.count("<") >= 2:
            out.append(line)
    return out


def _td3(l1: str, l2: str) -> bool:
    if len(l1) != 44 or len(l2) != 44 or l1[0] not in "PV":
        return False
    return _ok(l2[0:9], l2[9]) and _ok(l2[13:19], l2[19]) and _ok(l2[21:27], l2[27])


def _td2(l1: str, l2: str) -> bool:
    if len(l1) != 36 or len(l2) != 36 or l1[0] not in "ACIPV":
        return False
    return _ok(l2[0:9], l2[9]) and _ok(l2[13:19], l2[19]) and _ok(l2[21:27], l2[27])


def _td1(l1: str, l2: str) -> bool:
    if len(l1) != 30 or len(l2) != 30 or l1[0] not in "ACI":
        return False
    return _ok(l1[5:14], l1[14]) and _ok(l2[0:6], l2[6]) and _ok(l2[8:14], l2[14])


def find_mrz(text: str) -> list[str]:
    lines = _mrz_lines(text)
    hits: list[str] = []
    for a, b in pairwise(lines):
        if _td3(a, b) or _td2(a, b) or _td1(a, b):
            hits.extend((a, b))
    return hits


# ── numbers ──
_AADHAAR = re.compile(r"(?<![\d-])([2-9]\d{3})[ \-]?(\d{4})[ \-]?(\d{4})(?![\d-])")
_PAN = re.compile(r"\b([A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z])\b")
_PAN_LABEL = re.compile(r"permanent\s+account\s+number|income\s*tax\s+department|\bpan\b", re.IGNORECASE)
_SSN = re.compile(r"(?<!\d)((?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4})(?!\d)")
_SSN_LABEL = re.compile(r"social\s+security|\bssn\b", re.IGNORECASE)
_SEVIS = re.compile(r"\b(N\d{10})\b")
_VISA_NUMBER = re.compile(r"\b(\d{14}|[A-Z]\d{7}|\d{8})\b")
_VISA_LABEL = re.compile(
    r"\bvisa\s+(?:type|class|number|no\.?|foil|control)|control\s+number|annotation|\bi-20\b|\bds-?2019\b|\bsevis\b",
    re.IGNORECASE,
)

# ── documents ──
_PERSONAL_LABEL = re.compile(
    r"\b(?:surname|given\s+names?|date\s+of\s+birth|place\s+of\s+birth|nationality|father'?s\s+name"
    r"|name\s+of\s+(?:the\s+)?(?:candidate|student|holder|applicant)|received\s+(?:with\s+thanks\s+)?from"
    r"|account\s+(?:holder|number)|dob\s*[:/])",
    re.IGNORECASE,
)
_DOC_KEYWORDS: dict[str, re.Pattern[str]] = {
    "passport": re.compile(r"\bpassport\s+(?:no|number)|republic\s+of\s+india\b.*passport|\btype\s*/\s*code", re.I),
    "id_card": re.compile(
        r"\b(?:aadhaar|unique\s+identification|voter\s+id|driving\s+licen[cs]e|identity\s+card)\b", re.I
    ),
    "diploma": re.compile(
        r"\bthis\s+is\s+to\s+certify\b|\bdegree\s+of\s+(?:bachelor|master|doctor)|\bdiploma\b.*\bawarded\b"
        r"|\bconvocation\b|\bprovisional\s+(?:degree|certificate)\b|\bmark\s*s?heet\b",
        re.I,
    ),
    "visa": re.compile(r"\bvisa\s+(?:type|class|foil)\b|\bentries\b.*\bvisa\b|\bannotation\b", re.I),
    "i20": re.compile(r"\bform\s+i-20\b|\bcertificate\s+of\s+eligibility\b", re.I),
    "ds2019": re.compile(r"\bds-?2019\b", re.I),
    "receipt": re.compile(
        r"\b(?:fee\s+receipt|payment\s+receipt|transaction\s+(?:id|reference)|amount\s+paid|bank\s+statement)\b", re.I
    ),
}


@dataclass
class Sensitivity:
    signals: list[str] = field(default_factory=list)  # signal codes, e.g. "passport_mrz", "aadhaar"
    doc_type: str | None = None  # vault_documents.doc_type when stored in the vault
    redactions: set[str] = field(default_factory=set)

    @property
    def personal(self) -> bool:
        return bool(self.signals)

    def merge(self, other: Sensitivity) -> None:
        for s in other.signals:
            if s not in self.signals:
                self.signals.append(s)
        self.doc_type = self.doc_type or other.doc_type
        self.redactions |= other.redactions


_DOC_TYPE = {
    "passport_mrz": "passport",
    "aadhaar": "aadhaar",
    "pan": "pan",
    "ssn": "ssn",
    "sevis": "visa",
    "visa_number": "visa",
    "doc_passport": "passport",
    "doc_id_card": "id_card",
    "doc_diploma": "diploma",
    "doc_visa": "visa",
    "doc_i20": "i20",
    "doc_ds2019": "ds2019",
    "doc_receipt": "receipt",
}


def _near(text: str, start: int, end: int, label: re.Pattern[str], span: int = 160) -> bool:
    return bool(label.search(text[max(0, start - span) : end + span]))


def scan(text: str) -> Sensitivity:
    """Signals found in `text` (local, no model). Case of MRZ/PAN letters matters; other matches ignore case."""
    out = Sensitivity()
    if not text:
        return out

    def add(sig: str, value: str | None = None) -> None:
        if sig not in out.signals:
            out.signals.append(sig)
        out.doc_type = out.doc_type or _DOC_TYPE.get(sig)
        if value:
            out.redactions.add(value)

    for line in find_mrz(text):
        add("passport_mrz", line)
    for m in _AADHAAR.finditer(text):
        digits = "".join(m.groups())
        if verhoeff_valid(digits):
            add("aadhaar", m.group(0))
    for m in _PAN.finditer(text):
        if _near(text, m.start(), m.end(), _PAN_LABEL):
            add("pan", m.group(1))
    for m in _SSN.finditer(text):
        if _near(text, m.start(), m.end(), _SSN_LABEL):
            add("ssn", m.group(1))
    for m in _SEVIS.finditer(text):
        if _near(text, m.start(), m.end(), _VISA_LABEL):
            add("sevis", m.group(1))
    for m in _VISA_NUMBER.finditer(text):
        if _near(text, m.start(), m.end(), _VISA_LABEL, span=80):
            add("visa_number", m.group(1))
    if _PERSONAL_LABEL.search(text):
        for name, pat in _DOC_KEYWORDS.items():
            if pat.search(text):
                add(f"doc_{name}")
    return out


def redact(text: str, redactions: set[str]) -> str:
    """The second rules pass: every identifier found by the gate replaced by `[id]`."""
    for value in sorted(redactions, key=len, reverse=True):
        text = text.replace(value, "[id]")
    return text


__all__ = ["Sensitivity", "find_mrz", "mrz_check", "redact", "scan", "verhoeff_digit", "verhoeff_valid"]
