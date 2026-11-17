"""Last-writer-wins for rows a device edits offline (`user_pathway_tasks`, `costs`).

The stored row wins unless the incoming stamp is strictly newer (a tie goes to the server). An incoming stamp is
clamped to the server's now + 5 minutes so a device with a fast clock cannot freeze a row against everyone else.
Same semantics as S18's shared helper (`naadi_api/sync/lww.py::apply_lww`); this copy goes away when it lands.
"""

from __future__ import annotations

MAX_SKEW_MS = 5 * 60 * 1000


def clamp_stamp(incoming_ms: int, now_ms: int) -> int:
    return max(0, min(incoming_ms, now_ms + MAX_SKEW_MS))


def incoming_wins(incoming_ms: int, stored_ms: int | None, now_ms: int) -> bool:
    """True when the incoming write should replace the stored row."""
    return stored_ms is None or clamp_stamp(incoming_ms, now_ms) > stored_ms
