"""Tutor tools. Importing this package registers every tool (registry contract: ledger 20261117T033406).

Later segments add one import line each (S15: knowledge_ingest, S15b: generation) and own their module.
"""

from . import analytics, chapters, knowledge, media, memory, notes, study, web  # noqa: F401
from .registry import all_tools, get_tool, register_tool, tools_for

__all__ = ["all_tools", "get_tool", "register_tool", "tools_for"]
