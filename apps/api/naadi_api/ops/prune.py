"""Daily retention pass (segment 22, HANDOFF §2.1 mitigation 3): keeps Supabase Free under its 500 MB.

Run by the `prune` system cron (naadi_api.agent.graphs.prune, 04:11 UTC, after the nightly backup):

| table             | kept                                               | why                                         |
|-------------------|----------------------------------------------------|---------------------------------------------|
| `llm_usage`       | 90 days                                            | daily caps read today; trends a quarter     |
| `search_cache`    | the longest configured lifetime (cache_ttl_days,   | a row older than every layer's TTL is never |
|                   | every layer's ttl_hours; today 720 h)              | served again                                |
| `sync_ops`        | 24 hours                                           | offline-sync idempotency cache (S18)        |
| `sync_tombstones` | 90 days                                            | LWW deletes a device can still replay (S18) |
| `agent_health`    | 90 days                                            | hourly pings and job summaries              |
| `search_usage`    | 13 months                                          | the Tavily monthly stop reads this month    |

Aegra checkpoints expire through Aegra's own TTL sweep (aegra.json `checkpointer.ttl.default_ttl` = 43200 min =
30 days); ink tombstones through the ink GC (90 days, naadi_api.ink.store.TOMBSTONE_HORIZON_DAYS). A table that does
not exist yet (a segment not merged) is skipped, so the job never fails on a fresh or partial schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..logs import get_logger

log = get_logger("naadi.ops.prune")


@dataclass(frozen=True)
class Rule:
    table: str
    where: str  # a constant predicate on created_at; no user input ever reaches it

    def sql(self) -> str:
        return f"delete from public.{self.table} where {self.where}"  # noqa: S608 - module constants only


def search_cache_hours(models: Any) -> int:
    """The longest lifetime any search layer gives a cached row."""
    hours = [int(models.search.cache_ttl_days) * 24]
    for layer in (getattr(models.search, "layers", None) or {}).values():
        ttl = getattr(layer, "ttl_hours", None) if not isinstance(layer, dict) else layer.get("ttl_hours")
        if ttl:
            hours.append(int(ttl))
    return max(hours)


def rules(cache_hours: int) -> list[Rule]:
    if cache_hours < 1:
        raise ValueError("cache_hours must be positive")
    return [
        Rule("llm_usage", "created_at < now() - interval '90 days'"),
        Rule("search_cache", f"created_at < now() - interval '{int(cache_hours)} hours'"),
        Rule("sync_ops", "created_at < now() - interval '24 hours'"),
        Rule("sync_tombstones", "created_at < now() - interval '90 days'"),
        Rule("agent_health", "created_at < now() - interval '90 days'"),
        Rule("search_usage", "created_at < now() - interval '13 months'"),
    ]


async def prune(conn: Any, cache_hours: int) -> dict[str, int | None]:
    """Run every rule in its own statement; returns rows deleted per table (None: the table does not exist)."""
    out: dict[str, int | None] = {}
    for r in rules(cache_hours):
        cur = await conn.execute("select to_regclass(%s) is not null", (f"public.{r.table}",))
        row = await cur.fetchone()
        if not row or not row[0]:
            out[r.table] = None
            continue
        cur = await conn.execute(r.sql())
        out[r.table] = cur.rowcount
    log.info("prune.done", **{k: (v if v is not None else -1) for k, v in out.items()})
    return out
