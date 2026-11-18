"""Structured JSON logging to stdout (one JSON object per line).

Every stdlib logger (uvicorn, psycopg.pool, httpx) is routed through the same structlog formatter, so
the Space log is machine-readable end to end. Request context (`request_id`) is bound with contextvars
by the request middleware and merged into every line emitted while that request is handled.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from .scrub import redact

_CONFIGURED_LEVEL: str | None = None
# Secret values registered by the app factory; every rendered line is redacted against them (and against
# the secret-shaped patterns in scrub.py), including exception messages and formatted tracebacks.
_SECRETS: set[str] = set()


def register_secrets(values: list[str]) -> None:
    _SECRETS.update(v for v in values if v and len(v) >= 4)


def _redact_value(v: Any) -> Any:
    if isinstance(v, str):
        return redact(v, _SECRETS)
    if isinstance(v, dict):
        return {k: _redact_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_redact_value(x) for x in v]
    return v


def _redact_secrets(_: Any, __: str, event_dict: EventDict) -> EventDict:
    """Runs after exceptions are formatted, so tracebacks are covered too."""
    for k, v in list(event_dict.items()):
        event_dict[k] = _redact_value(v)
    return event_dict


# Keys that must never appear in a log line, whatever a caller passes.
_FORBIDDEN_KEYS = frozenset(
    {"body", "request_body", "response_body", "authorization", "cookie", "password", "token", "query", "query_string"}
)


def _drop_forbidden(_: Any, __: str, event_dict: EventDict) -> EventDict:
    for k in list(event_dict):
        if k.lower() in _FORBIDDEN_KEYS:
            event_dict.pop(k)
    return event_dict


def _shared_processors() -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        _drop_forbidden,
    ]


def json_formatter() -> logging.Formatter:
    """The one JSON line formatter. Also referenced by `uvicorn_logging.json`, so uvicorn's own lines
    (including the parent process of `--workers N`, which never imports the app) are JSON too."""
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            _redact_secrets,
            structlog.processors.JSONRenderer(sort_keys=False),
        ],
    )


def configure_logging(level: str = "INFO", stream: Any = None) -> None:
    """Idempotent: safe to call from every app factory (tests create many apps)."""
    global _CONFIGURED_LEVEL
    lvl = getattr(logging, level.upper(), logging.INFO)

    formatter = json_formatter()
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(lvl)
    # Uvicorn installs its own handlers before importing the app; send its records through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    # Access lines come from our middleware (no query strings); uvicorn's would include them.
    logging.getLogger("uvicorn.access").disabled = True
    # httpx logs full URLs at INFO, which can include query-string tokens.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)

    if _CONFIGURED_LEVEL is None or level != _CONFIGURED_LEVEL:
        structlog.configure(
            processors=[
                *_shared_processors(),
                structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
            ],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=False,
        )
        _CONFIGURED_LEVEL = level


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger
