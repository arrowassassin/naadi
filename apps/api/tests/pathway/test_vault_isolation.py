"""The vault never reaches a model, a search provider, an embeddings index or a log: an import-graph test.

Every module of the vault (`vault.py`, `vault_routes.py`, `storage.py`, and what they import inside `naadi_api`) is
walked transitively; none may reach `naadi_api.llm`, `naadi_api.search`, the embeddings package, or the LLM-facing
`llm_*` / agent modules."""

from __future__ import annotations

import ast
from pathlib import Path

import naadi_api

ROOT = Path(naadi_api.__file__).resolve().parent
VAULT_ENTRY = ("naadi_api.pathway.vault", "naadi_api.pathway.vault_routes", "naadi_api.pathway.storage")
# Whole modules (and what lives under them). `naadi_api.llm_config` is not here: it only holds the pydantic models
# of models.yaml that the generic config service parses; it makes no call and holds no key.
FORBIDDEN = (
    "naadi_api.llm",
    "naadi_api.llm_budget",
    "naadi_api.llm_privacy",
    "naadi_api.search",
    "naadi_api.search_routes",
    "naadi_api.embeddings",
    "naadi_api.agent",
    "naadi_api.coach",
    "naadi_api.pathway.verify",
    "langchain",
    "langgraph",
    "openrouter",
    "tavily",
    "fastembed",
)


def forbidden(name: str) -> bool:
    return any(name == f or name.startswith(f + ".") for f in FORBIDDEN)


def module_path(name: str) -> Path | None:
    parts = name.split(".")[1:]
    if not parts:
        return ROOT / "__init__.py"
    rel = Path(*parts)
    for cand in (ROOT / rel.with_suffix(".py"), ROOT / rel / "__init__.py"):
        if cand.exists():
            return cand
    return None


def imports_of(path: Path, package: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - node.level + 1]
                prefix = ".".join(base + ([node.module] if node.module else []))
            else:
                prefix = node.module or ""
            found.add(prefix)
            found.update(f"{prefix}.{a.name}" for a in node.names)
    return found


def closure(entry: tuple[str, ...]) -> dict[str, set[str]]:
    seen: dict[str, set[str]] = {}
    todo = list(entry)
    while todo:
        name = todo.pop()
        if name in seen or not name.startswith("naadi_api"):
            continue
        path = module_path(name)
        if path is None:
            continue
        package = name if path.name == "__init__.py" else name.rsplit(".", 1)[0]
        seen[name] = imports_of(path, package)
        todo.extend(i for i in seen[name] if i.startswith("naadi_api"))
        # importing a.b.c also runs a/__init__ and a/b/__init__
        for imp in list(seen[name]):
            parts = imp.split(".")
            todo.extend(".".join(parts[:i]) for i in range(2, len(parts)))
    return seen


def test_the_vault_import_graph_reaches_no_model_search_or_index() -> None:
    graph = closure(VAULT_ENTRY)
    assert set(VAULT_ENTRY) <= set(graph)
    offenders = {
        mod: sorted(i for i in imps if forbidden(i)) for mod, imps in graph.items() if any(forbidden(i) for i in imps)
    }
    assert offenders == {}, f"the vault must not import: {offenders}"


def test_the_guard_would_catch_a_violation(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text("from ..llm import get_gateway\nimport naadi_api.search\n")
    imps = imports_of(f, "naadi_api.pathway")
    assert any(forbidden(i) for i in imps) and forbidden("naadi_api.search") and not forbidden("naadi_api.llm_config")


def test_the_vault_modules_do_not_log_arguments() -> None:
    for name in ("vault.py", "vault_routes.py", "storage.py"):
        src = (ROOT / "pathway" / name).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"info", "warning", "error", "debug", "exception"}
            ):
                owner = node.func.value
                if isinstance(owner, ast.Name) and owner.id == "log":
                    keys = {k.arg for k in node.keywords}
                    assert keys <= {"op", "status", "error"}, (name, keys)
