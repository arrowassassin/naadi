"""Resumable eval: finished items are kept in a checkpoint file so a run cut short by a daily cap is completed by a
later run (e.g. the next UTC day) without spending any call twice on an item that already finished.

An item counts as finished only when it can count towards the bar as it is: an MCQ without an error, a prompt that
was answered, machine-checked and graded (rubric mean set, no error, no grading error). Anything else runs again.

The checkpoint is bound to a key over everything that decides an item's result: the prompts file, the answerer's
code (its system prompt lives there), the safety checks and grader, the model roles in config/models.yaml, the
held-out ids and k. Change any of them and the old checkpoint is set aside and the run starts afresh.

Stored answers are the redacted ones the report keeps (values a prompt marks as PII replaced); nothing else is
written. The file lives under .tmp/ (git-ignored).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .report import McqItem, PromptItem
from .safety import CheckResult

VERSION = 1
HERE = Path(__file__).resolve().parent
# Source files whose content decides an item's result (besides the prompts file and the model config).
KEYED_SOURCES = ("answerers.py", "safety.py", "grader.py", "retrieval.py", "heldout.py", "prompts.py")


def run_key(
    *,
    prompts_file: Path,
    models_file: Path,
    answerer: str,
    k: int,
    heldout_ids: Iterable[str],
    sources_dir: Path = HERE,
) -> str:
    h = hashlib.sha256()
    h.update(f"v{VERSION}|{answerer}|k={k}|".encode())
    h.update(",".join(sorted(heldout_ids)).encode())
    for path in (prompts_file, models_file, *(sources_dir / s for s in KEYED_SOURCES)):
        h.update(b"|" + path.name.encode() + b"=")
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def mcq_finished(item: McqItem) -> bool:
    return item.error is None and item.chosen is not None


def prompt_finished(item: PromptItem) -> bool:
    return item.error is None and item.grade_error is None and item.rubric_mean is not None


@dataclass
class Checkpoint:
    path: Path
    key: str
    mcq: dict[str, dict[str, Any]] = field(default_factory=dict)
    prompts: dict[str, dict[str, Any]] = field(default_factory=dict)
    days: list[str] = field(default_factory=list)
    set_aside: str | None = None  # why an earlier checkpoint was not used

    @classmethod
    def load(cls, path: Path, key: str) -> Checkpoint:
        if not path.is_file():
            return cls(path, key)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(path, key, set_aside="unreadable checkpoint")
        if raw.get("version") != VERSION or raw.get("key") != key:
            return cls(path, key, set_aside="prompts, answerer, checks or model config changed since the checkpoint")
        return cls(
            path,
            key,
            mcq=dict(raw.get("mcq") or {}),
            prompts=dict(raw.get("prompts") or {}),
            days=list(raw.get("days") or []),
        )

    # ── reading ──

    def mcq_item(self, qid: str) -> McqItem | None:
        d = self.mcq.get(qid)
        return McqItem(**d) if d else None

    def prompt_item(self, pid: str) -> PromptItem | None:
        d = self.prompts.get(pid)
        if not d:
            return None
        d = dict(d)
        d["checks"] = [CheckResult(**c) for c in d.get("checks") or []]
        return PromptItem(**d)

    # ── writing ──

    def record(self, item: McqItem | PromptItem) -> bool:
        """Keep a finished item (and save at once, so a crash loses nothing). False when it is not finished."""
        if isinstance(item, McqItem):
            if not mcq_finished(item):
                return False
            self.mcq[item.id] = asdict(item)
        else:
            if not prompt_finished(item):
                return False
            d = asdict(item)
            d["checks"] = [c.to_dict() for c in item.checks]
            self.prompts[item.id] = d
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        if day not in self.days:
            self.days.append(day)
        self.save()
        return True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        body = {"version": VERSION, "key": self.key, "days": self.days, "mcq": self.mcq, "prompts": self.prompts}
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


__all__ = ["Checkpoint", "mcq_finished", "prompt_finished", "run_key"]
