"""Placement handlers. Each module registers its kinds on import; add one import line per module (S16/S17/S20 own
theirs, e.g. `from . import planner  # noqa: F401`)."""

from __future__ import annotations

from .builtin import BUILTINS
from .registry import (
    Candidate,
    IngestView,
    PlacementContext,
    PlacementHandler,
    PlacementRecord,
    PlacementRegistryError,
    accepted_inputs,
    handler_for,
    propose_all,
    register_placement,
    registered,
)

for _h in BUILTINS:
    register_placement(_h.kind, _h)

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
]
