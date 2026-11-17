"""The tutor guard's Python rules (HANDOFF §6.2: rules first, the `router` role only when they are ambiguous).

Exam mode is not decided here: the graph reads `active_exam` on the server. These rules pick the turn's intent so
the system prompt carries the right safety rule (§6.5):
- `pathway`: residency, visa, exam registration → answer from the pathway guide (`search_knowledge` scope
  pathway, with `last_verified`) plus official domains only, always link the official page (rule 5);
- `dosing`: doses, regimens → no numbers from memory: "I don't know" and offer a search of drug labels (rule 6);
- `copyright`: asks to reproduce a question bank or review book → summarise instead (rule 3);
- `study`: everything else.
"""

from __future__ import annotations

import re
from typing import Literal

Intent = Literal["study", "pathway", "dosing", "copyright"]

_PATHWAY = re.compile(
    r"\b(visa|h-?1b|j-?1|ecfmg|intealth|eras|nrmp|the match|residency application|usce|observership|"
    r"step\s*(?:1|2|3)?\s*(?:registration|eligibility|fee|scheduling)|green card|uscis|consulate|"
    r"letters? of recommendation|lor|personal statement|oasis)\b",
    re.IGNORECASE,
)
_DOSING = re.compile(
    r"\b(dose|doses|dosing|dosage|mg/kg|mcg/kg|units/kg|how (?:much|many) (?:mg|units|ml)|loading dose|"
    r"titrat\w*|regimen|max(?:imum)? daily|infusion rate)\b",
    re.IGNORECASE,
)
_COPYRIGHT = re.compile(
    r"\b(first aid|uworld|nbme|amboss|kaplan|pathoma|sketchy|boards and beyond)\b.*\b(text|page|copy|paste|quote|"
    r"verbatim|question|questions|exact)\b|\b(copy|paste|quote|reproduce|verbatim)\b.*\b(first aid|uworld|nbme|"
    r"amboss|kaplan|pathoma)\b",
    re.IGNORECASE,
)


def rule_intents(text: str) -> set[Intent]:
    found: set[Intent] = set()
    if _COPYRIGHT.search(text):
        found.add("copyright")
    if _PATHWAY.search(text):
        found.add("pathway")
    if _DOSING.search(text):
        found.add("dosing")
    return found


def decide(text: str) -> tuple[Intent | None, bool]:
    """(intent, ambiguous). `ambiguous` = more than one competing intent; the router role may break the tie."""
    found = rule_intents(text)
    if "copyright" in found:
        return "copyright", False  # the strictest rule wins outright
    if len(found) > 1:
        return None, True
    if found:
        return next(iter(found)), False
    return "study", False


def parse_router(reply: str) -> Intent:
    word = reply.strip().lower()
    for intent in ("pathway", "dosing", "copyright", "study"):
        if intent in word:
            return intent
    return "dosing"  # unreadable reply: the safer of the competing rules


ROUTER_PROMPT = (
    "Classify the student's message for a medical study assistant. Reply with exactly one word: pathway (residency, "
    "visa or exam registration logistics), dosing (drug doses or regimens), or study (anything else)."
)
