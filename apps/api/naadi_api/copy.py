"""Server-side `ui_copy`: the few strings that leave the server as text (calendar feed, push notifications) are
worded from the same `app_config.ui_copy` templates as the web app, with `{name}` placeholders."""

from __future__ import annotations

import re
from collections.abc import Mapping

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def render(copy: Mapping[str, str], key: str, values: Mapping[str, str | int | float] | None = None) -> str:
    """The template for `key` with `{name}` replaced from `values`. A missing key renders as the key itself and a
    missing value as an empty string (never an exception: a feed or a push must not fail over wording)."""
    template = copy.get(key, key)
    vals = values or {}
    return _PLACEHOLDER.sub(lambda m: str(vals.get(m.group(1), "")), template)
