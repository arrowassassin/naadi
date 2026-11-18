"""The seed verification report (printed by scripts/seed.py, stored in seed_runs.report, served to admins)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TableCounts(BaseModel):
    total: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    kept: int = Field(default=0, description="Rows an admin edited, left as they are")
    deleted: int = Field(default=0, description="Derived rows the data no longer has (placements, plan days)")

    @property
    def changes(self) -> int:
        return self.inserted + self.updated + self.deleted


class StorageCounts(BaseModel):
    uploaded: int = 0
    unchanged: int = 0


class Check(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class SeedReport(BaseModel):
    dry_run: bool
    groups: list[str]
    tables: dict[str, TableCounts] = Field(default_factory=dict)
    storage: StorageCounts = Field(default_factory=StorageCounts)
    license_gate: dict[str, Any] = Field(default_factory=dict)
    svg: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, int] = Field(default_factory=dict)
    actual: dict[str, int] = Field(default_factory=dict)
    checks: list[Check] = Field(default_factory=list)
    seconds: float = 0.0
    ok: bool = False

    @property
    def changes(self) -> int:
        return sum(t.changes for t in self.tables.values()) + self.storage.uploaded

    def summary(self) -> str:
        lines = [f"seed {'DRY RUN (nothing written)' if self.dry_run else 'run'}: groups {', '.join(self.groups)}"]
        lines.append(f"{'table':<28}{'total':>7}{'insert':>8}{'update':>8}{'same':>7}{'kept':>6}{'delete':>8}")
        for name, t in self.tables.items():
            lines.append(
                f"{name:<28}{t.total:>7}{t.inserted:>8}{t.updated:>8}{t.unchanged:>7}{t.kept:>6}{t.deleted:>8}"
            )
        verb = "would upload" if self.dry_run else "uploaded"
        lines.append(f"storage: {verb} {self.storage.uploaded}, unchanged {self.storage.unchanged}")
        rejected = self.license_gate.get("rejected") or []
        lines.append(
            f"license gate: {len(self.license_gate.get('accepted') or [])} sources accepted, {len(rejected)} rejected"
        )
        for r in rejected:
            lines.append(f"  rejected {r['id']}: {r['reason']}")
        lines.append(f"svg: {self.svg.get('fixed', 0)} fixes, {len(self.svg.get('removed') or [])} removals")
        for r in self.svg.get("removed") or []:
            lines.append(f"  removed {r}")
        for c in self.checks:
            lines.append(f"[{'ok' if c.ok else 'FAIL'}] {c.name}: {c.detail}")
        lines.append(f"changes: {self.changes} · {'OK' if self.ok else 'FAILED'} in {self.seconds:.1f}s")
        return "\n".join(lines)
