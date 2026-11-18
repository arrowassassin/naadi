"""Static guard (review15): students' private questions (`questions.owner_user_id`, S15's private_questions
placement) never reach another student, an admin list, the authoring queues, psychometrics or coverage.

Every SQL statement that reads or writes `public.questions` must either carry an `owner_user_id` predicate itself
or sit under a `# questions-scope: <why>` comment saying why it needs none: it runs as the student under RLS (whose
per-role policies show shared rows plus the caller's own), it only inserts shared rows, it checks id uniqueness
across every row, or it reads the seed bundle's own ids. A new query without either fails here.

Statements are read from the AST, so adjacent literals, triple-quoted SQL, f-strings and `+` concatenations are
each seen as one statement.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "naadi_api"
TABLE = re.compile(r"\b(from|join|update|into)\s+public\.questions\b", re.I)
ALIASED = re.compile(r"\b(?:from|join|update)\s+public\.questions(?:\s+(?:as\s+)?([a-z_][a-z0-9_]*))?", re.I)
NOT_ALIAS = {
    "where",
    "on",
    "set",
    "join",
    "left",
    "right",
    "inner",
    "full",
    "cross",
    "order",
    "group",
    "limit",
    "for",
    "using",
    "union",
    "returning",
    "natural",
    "window",
    "offset",
}
SCOPE = "questions-scope:"
ALLOWED_SCOPES = ("rls", "insert", "id-unique", "seed", "write-only")


def _text(node: ast.AST) -> str | None:
    """The SQL text of a string expression: literal parts joined, placeholders for interpolations."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)  # type: ignore[misc]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text(node.left), _text(node.right)
        if left is not None or right is not None:
            return (left or "{}") + (right or "{}")
    return None


def statements(path: Path) -> list[tuple[int, int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[int, int, str]] = []
    inner: set[int] = set()
    for node in ast.walk(tree):
        if id(node) in inner:
            continue
        sql = _text(node)
        if sql is None:
            continue
        for child in ast.walk(node):
            if child is not node:
                inner.add(id(child))
        if TABLE.search(sql):
            out.append((node.lineno, node.end_lineno or node.lineno, sql))
    return out


def owner_scoped(sql: str) -> bool:
    """Each UNION branch that touches public.questions filters on the questions row's own owner_user_id: qualified
    by its alias when it has one, unqualified otherwise (another table's c.owner_user_id does not count)."""
    for part in re.split(r"\bunion\b(?:\s+all)?", sql, flags=re.I):
        if not TABLE.search(part):
            continue
        if re.search(r"\binto\s+public\.questions\s*\([^)]*\bowner_user_id\b", part, re.I):
            continue  # an insert that names the owner column
        refs = [m.group(1) for m in ALIASED.finditer(part)]
        if not refs:
            return False
        for alias in refs:
            if alias and alias.lower() not in NOT_ALIAS:
                if not re.search(rf"\b{re.escape(alias)}\.owner_user_id\b", part):
                    return False
            elif not re.search(r"(?<![\w.])owner_user_id\b|\bquestions\.owner_user_id\b", part):
                return False
    return True


def _scope_comment(lines: list[str], start: int, end: int) -> str | None:
    # On the statement's lines, or in the comment block right above it (or above its assignment).
    for i in range(end, max(0, start - 6), -1):
        line = lines[i - 1]
        if SCOPE in line:
            return line.split(SCOPE, 1)[1].strip()
    return None


def offenders() -> list[str]:
    bad: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for start, end, sql in statements(path):
            if owner_scoped(sql):
                continue
            why = _scope_comment(lines, start, end)
            rel = f"{path.relative_to(ROOT).as_posix()}:{start}"
            if why is None:
                bad.append(f"{rel}: no owner_user_id predicate and no '# {SCOPE}' reason")
            elif not why.startswith(ALLOWED_SCOPES):
                bad.append(f"{rel}: unknown scope {why!r} (one of {ALLOWED_SCOPES})")
    return bad


def test_every_query_on_questions_is_owner_scoped_or_says_why_not() -> None:
    assert offenders() == []


def test_the_guard_sees_every_statement_shape(tmp_path: Path) -> None:
    src = tmp_path / "m.py"
    src.write_text(
        "A = '''\nselect id from public.questions q\n'''\n"
        'B = ("select id from public.questions"\n " where true")\n'
        'def f(x):\n    return f"select {x} from public.questions where id = %s" + " for update"\n'
        "C = 'select id from public.questions where owner_user_id is null'\n"
        "# questions-scope: rls (user_tx)\n"
        "D = 'select id from public.questions'\n"
    )
    found = sorted(statements(src))
    assert [s[0] for s in found] == [1, 4, 7, 8, 10]
    assert [owner_scoped(s[2]) for s in found] == [False, False, False, True, False]
    # Another table's owner column does not scope the questions branch of a union.
    assert not owner_scoped(
        "select 1 from public.chapters c where c.owner_user_id is null"
        " union all select 2 from public.questions q where q.status = 'published'"
    )
    assert owner_scoped(
        "select 1 from public.questions q join public.chapters c on c.id = q.chapter_id"
        " where q.owner_user_id is null and c.owner_user_id is null"
    )
    assert not owner_scoped(
        "select 1 from public.questions q join public.chapters c on true where c.owner_user_id is null"
    )
    assert owner_scoped("update public.questions set stats = %s where id = %s and owner_user_id is null")
    lines = src.read_text().splitlines()
    assert _scope_comment(lines, 10, 10) == "rls (user_tx)"
    assert _scope_comment(lines, 1, 3) is None
