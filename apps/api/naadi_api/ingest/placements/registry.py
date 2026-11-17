"""Placement handler registry (ledger contract 20261117T042803-seg15-ingest-47a357).

    register_placement(kind, handler, requires_flag=None)

A handler proposes where an ingested item could go (`propose`, pure), writes it after the student confirms
(`apply`, inside one service-role transaction) and removes exactly what it wrote (`undo`). S15 registers the
built-in kinds; S16 (planner items), S17 (cost entries) and S20 (Anki decks) register theirs from their own modules,
imported in `placements/__init__.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import psycopg

from ..extract.model import ExtractedDoc
from ..understand import Understanding

KIND_PATTERN = r"^[a-z][a-z0-9_]{1,39}$"


@dataclass(frozen=True)
class IngestView:
    """What a handler may read. Built after the sensitivity gate (never for a vaulted item); identifiers the gate
    found are already redacted from `doc`."""

    item_id: uuid.UUID
    user_id: uuid.UUID
    input_kind: str
    understanding: Understanding
    doc: ExtractedDoc
    original_name: str
    flags: Mapping[str, bool] = field(default_factory=dict)
    # Storage path of the original file (`user-uploads/...`) when one is kept (PDFs, images); None for links/pastes.
    original_path: str | None = None
    # Matching shared sections for this item (id, chapter title, heading, score), from local retrieval.
    matches: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class Candidate:
    kind: str
    score: float
    preview: dict[str, Any]
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlacementContext:
    conn: psycopg.AsyncConnection[Any]  # service role, row_factory = dict_row, inside the placing transaction
    user_id: uuid.UUID
    item_id: uuid.UUID
    source_id: str
    view: IngestView


@dataclass(frozen=True)
class PlacementRecord:
    table: str
    id: str
    kind: str

    def to_json(self) -> dict[str, str]:
        return {"table": self.table, "id": self.id, "kind": self.kind}

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> PlacementRecord:
        return cls(table=str(raw["table"]), id=str(raw["id"]), kind=str(raw["kind"]))


@runtime_checkable
class PlacementHandler(Protocol):
    kind: str
    inputs: frozenset[str]

    def propose(self, view: IngestView) -> list[Candidate]: ...

    async def apply(self, ctx: PlacementContext, cand: Candidate) -> list[PlacementRecord]: ...

    async def undo(self, ctx: PlacementContext, records: list[PlacementRecord]) -> None: ...


@dataclass(frozen=True)
class Registered:
    kind: str
    handler: PlacementHandler
    requires_flag: str | None
    order: int


_REGISTRY: dict[str, Registered] = {}


class PlacementRegistryError(ValueError):
    pass


def register_placement(kind: str, handler: PlacementHandler, *, requires_flag: str | None = None) -> None:
    import re

    if not re.match(KIND_PATTERN, kind):
        raise PlacementRegistryError(f"invalid placement kind {kind!r}")
    if kind in _REGISTRY:
        raise PlacementRegistryError(f"placement kind {kind!r} is already registered")
    if getattr(handler, "kind", None) != kind:
        raise PlacementRegistryError(f"handler.kind must be {kind!r}")
    if not isinstance(handler, PlacementHandler):
        raise PlacementRegistryError(f"{kind!r} does not implement PlacementHandler (propose, apply, undo, inputs)")
    _REGISTRY[kind] = Registered(kind, handler, requires_flag, len(_REGISTRY))


def unregister_placement(kind: str) -> None:
    """Tests only."""
    _REGISTRY.pop(kind, None)


def registered(flags: Mapping[str, bool] | None = None) -> list[Registered]:
    """Registered handlers in registration order; with `flags`, only those whose flag is on."""
    out = sorted(_REGISTRY.values(), key=lambda r: r.order)
    if flags is None:
        return out
    return [r for r in out if r.requires_flag is None or flags.get(r.requires_flag, False)]


def handler_for(kind: str, flags: Mapping[str, bool]) -> PlacementHandler | None:
    r = _REGISTRY.get(kind)
    if r is None or (r.requires_flag is not None and not flags.get(r.requires_flag, False)):
        return None
    return r.handler


def accepted_inputs(flags: Mapping[str, bool]) -> list[str]:
    """Input kinds some enabled handler takes on its own (e.g. `anki` once S20 registers), for the drop zone."""
    out: set[str] = set()
    for r in registered(flags):
        out |= set(r.handler.inputs)
    return sorted(out)


def propose_all(view: IngestView) -> list[tuple[Registered, Candidate]]:
    """Every enabled handler's candidates, best first (ties: registration order). Handlers that raise are skipped."""
    from ...logs import get_logger

    log = get_logger("naadi.ingest.placements")
    out: list[tuple[Registered, Candidate]] = []
    for r in registered(view.flags):
        try:
            cands = r.handler.propose(view)
        except Exception as exc:  # noqa: BLE001 - one broken handler must not block the item
            log.warning("ingest.propose_failed", kind=r.kind, error=type(exc).__name__)
            continue
        for c in cands:
            if c.kind != r.kind or not 0.0 <= c.score <= 1.0:
                log.warning("ingest.propose_invalid", kind=r.kind)
                continue
            out.append((r, c))
    out.sort(key=lambda rc: (-rc[1].score, rc[0].order))
    return out


__all__ = [
    "Candidate",
    "IngestView",
    "PlacementContext",
    "PlacementHandler",
    "PlacementRecord",
    "PlacementRegistryError",
    "accepted_inputs",
    "handler_for",
    "propose_all",
    "register_placement",
    "registered",
    "unregister_placement",
]
