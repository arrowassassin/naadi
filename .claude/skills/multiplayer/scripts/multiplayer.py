#!/usr/bin/env python3
"""multiplayer: a task-distributor ledger (git branch) + one shared commit branch for parallel agents.

Standard library only (Python 3.10+). Every write is: pull --rebase -> add new file(s) -> commit -> push,
retried with backoff. Writers only ever ADD files (one file per message, one per ack), so concurrent
writers never produce merge conflicts on the ledger branch.

Flow: the lead posts a PLAN and ASSIGNs one task per agent on the ledger -> each agent ACCEPTs, posts UPDATEs,
messages/acks peers, claims paths, INTEGRATEs commits onto the shared branch, marks DONE -> the lead VERIFYs and PROMOTEs.

Quick reference (run `multiplayer.py <cmd> -h` for options):
  plan     --as lead           post the distribution plan (waves, owners, dependencies) for everyone
  assign   --as lead --to A    create a task for agent A (id, goal, acceptance, paths, deps)
  accept   --as A TASK         agent takes the task (or `decline` with a reason)
  update   --as A TASK         progress note on a task (percent, what's next, risks)
  done     --as A TASK         task finished (commit refs); lead then runs `verify`
  verify   --as lead TASK      lead accepts or reopens a finished task
  tasks                        task board: owner, status, deps, last update
  init                         create the ledger branch on the remote if missing
  hello    --as A --role R     register an agent (segment, worktree, purpose)
  post     --as A --type T     post a message (claim/contract/question/answer/block/conflict/handoff/status/done/...)
  ack      --as A ID...        acknowledge messages
  inbox    --as A              messages for A that A hasn't acked (exit 0; prints JSON lines or a table)
  show     ID                  one message with its acks and replies
  thread   ID                  whole thread
  claim    --as A --paths P..  advisory lock on paths (exit 3 if another agent holds an overlapping claim)
  release  --as A CLAIM_ID     release a claim
  claims                       active claims
  wait     --as A ...          block until a matching message arrives (answer/decision/unblock/...)
  board                        status board: agents, open questions, blocks, claims, unacked
  integrate --as A             rebase your local work onto the integration branch and push it (exit 4 on conflict,
                               which is auto-posted to the ledger)
  promote  --to BRANCH         lead only: merge the integration branch into BRANCH (no-ff) and push
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import random
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

TYPES = {
    "plan", "task", "accept", "decline", "update", "verify", "reopen",
    "hello", "claim", "release", "contract", "decision", "question", "answer", "block", "unblock",
    "conflict", "handoff", "status", "review-request", "done", "nudge", "note",
}
NEEDS_REPLY = {"question", "contract", "block", "conflict", "review-request", "handoff", "task"}
def _repo_config() -> dict:
    """Per-repo defaults from .claude/multiplayer.toml (committed), overridable by env vars and flags."""
    try:
        import tomllib  # stdlib, Python 3.11+
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], text=True, capture_output=True).stdout.strip()
        with open(Path(top) / ".claude" / "multiplayer.toml", "rb") as f:
            return tomllib.load(f).get("multiplayer", {})
    except (OSError, ImportError, ValueError):
        return {}


_CFG = _repo_config()
DEFAULTS = {
    "remote": os.environ.get("MP_REMOTE", _CFG.get("remote", "origin")),
    "ledger_branch": os.environ.get("MP_LEDGER_BRANCH", _CFG.get("ledger_branch", "multiplayer-ledger")),
    "integration_branch": os.environ.get("MP_SHARED_BRANCH", _CFG.get("shared_branch", "multiplayer-shared")),
}


# ───────────────────────── git helpers ─────────────────────────
def git(*args: str, cwd: str | Path | None = None, check: bool = True, quiet: bool = False) -> subprocess.CompletedProcess:
    p = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)
    if check and p.returncode != 0:
        if not quiet:
            sys.stderr.write(f"git {' '.join(args)} failed:\n{p.stderr.strip()}\n")
        raise SystemExit(2)
    return p


def repo_root() -> Path:
    return Path(git("rev-parse", "--show-toplevel").stdout.strip())


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


class Ledger:
    """Reads and writes the ledger branch WITHOUT checking it out.

    Writes build a commit with plumbing (temporary index -> write-tree -> commit-tree) on top of the remote tip
    and push it; a rejected push means someone else wrote first, so rebuild on the new tip and retry. Because
    writers only add new files, a rebuild never conflicts. Each process fetches into its own private ref, so
    any number of agents, in any number of worktrees or clones, can post at the same time without locks.
    """

    def __init__(self, ns: argparse.Namespace):
        self.root = repo_root()
        self.remote = ns.remote
        self.branch = ns.ledger_branch
        self.integration = ns.integration_branch
        key = re.sub(r"[^a-z0-9._-]", "-", (getattr(ns, "as_", None) or "reader").lower())
        self.ref = f"refs/multiplayer/{key}-{os.getpid()}"
        self._cache: dict[str, str] | None = None

    # ── remote tip ──
    def fetch(self) -> str | None:
        for attempt in range(5):
            r = git("fetch", "-q", "--no-tags", self.remote, f"+refs/heads/{self.branch}:{self.ref}", cwd=self.root, check=False)
            if r.returncode == 0:
                self._cache = None
                return git("rev-parse", self.ref, cwd=self.root).stdout.strip()
            if "couldn't find remote ref" in r.stderr or "not found" in r.stderr.lower():
                return None
            time.sleep(0.3 * (attempt + 1) + random.random() * 0.3)
        sys.stderr.write(f"ledger: fetch failed: {r.stderr.strip()[:200]}\n")
        raise SystemExit(2)

    def ensure(self) -> None:
        if self.fetch() is None:
            files = {"README.md": LEDGER_README.format(integration=self.integration),
                     "messages/.keep": "", "acks/.keep": "", "agents/.keep": ""}
            self._commit_and_push(files, "ledger: init", parent=None)

    def pull(self) -> None:
        if self.fetch() is None:
            self.ensure()
            self.fetch()

    def cleanup(self) -> None:
        git("update-ref", "-d", self.ref, cwd=self.root, check=False)

    # ── writes ──
    def _commit_and_push(self, files: dict[str, str], message: str, parent: str | None) -> bool:
        env = dict(os.environ)
        idx = Path(self.root / ".git").resolve() if (self.root / ".git").is_dir() else None
        tmp_index = f"/tmp/multiplayer-index-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        env["GIT_INDEX_FILE"] = tmp_index
        try:
            def g(*args, inp=None):
                p = subprocess.run(["git", *args], cwd=self.root, env=env, text=True, input=inp, capture_output=True)
                if p.returncode != 0:
                    raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()[:200]}")
                return p.stdout.strip()
            if parent:
                g("read-tree", parent)
            else:
                g("read-tree", "--empty")
            for rel, content in files.items():
                sha = g("hash-object", "-w", "--stdin", inp=content)
                g("update-index", "--add", "--cacheinfo", f"100644,{sha},{rel}")
            tree = g("write-tree")
            commit = g("commit-tree", tree, *(["-p", parent] if parent else []), "-m", message)
            p = subprocess.run(["git", "push", "-q", self.remote, f"{commit}:refs/heads/{self.branch}"],
                               cwd=self.root, text=True, capture_output=True)
            return p.returncode == 0
        finally:
            try:
                os.remove(tmp_index)
            except OSError:
                pass
            _ = idx

    def write(self, files: dict[str, str], message: str) -> None:
        """Add new files and push, retrying on races. Files are never edited, only added."""
        for attempt in range(10):
            tip = self.fetch()
            if tip is None:
                self.ensure()
                continue
            if self._commit_and_push(files, message, tip):
                self._cache = None
                return
            time.sleep(min(20, 0.4 * (2 ** attempt)) * (0.5 + random.random()))
        sys.stderr.write("ledger: push failed after retries (network or permissions)\n")
        raise SystemExit(2)

    # ── reads (from the fetched private ref) ──
    def _files(self) -> dict[str, str]:
        if self._cache is not None:
            return self._cache
        if git("rev-parse", "-q", "--verify", self.ref, cwd=self.root, check=False).returncode != 0:
            self.pull()
        names = [n for n in git("ls-tree", "-r", "--name-only", self.ref, cwd=self.root).stdout.splitlines() if n.endswith(".json")]
        out: dict[str, str] = {}
        if names:
            spec = "".join(f"{self.ref}:{n}\n" for n in names)
            p = subprocess.run(["git", "cat-file", "--batch"], cwd=self.root, input=spec.encode(), capture_output=True)
            data = p.stdout
            i = 0
            for n in names:
                nl = data.index(b"\n", i)
                header = data[i:nl].split()
                size = int(header[2])
                out[n] = data[nl + 1: nl + 1 + size].decode("utf-8", "replace")
                i = nl + 1 + size + 1
        self._cache = out
        return out

    def messages(self) -> list[dict]:
        out = []
        for name, txt in self._files().items():
            if name.startswith("messages/"):
                try:
                    out.append(json.loads(txt))
                except json.JSONDecodeError:
                    continue
        return sorted(out, key=lambda m: m["ts"])

    def acks(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for name in self._files():
            if name.startswith("acks/"):
                _, mid, who = name.split("/", 2)
                out.setdefault(mid, set()).add(who[:-5])
        return out

    def agents(self) -> dict[str, dict]:
        out = {}
        for name, txt in self._files().items():
            if name.startswith("agents/"):
                try:
                    out[name[7:-5]] = json.loads(txt)
                except json.JSONDecodeError:
                    pass
        return out

    def get(self, mid: str) -> dict:
        for m in self.messages():
            if m["id"] == mid or m["id"].startswith(mid):
                return m
        sys.stderr.write(f"no message {mid}\n")
        raise SystemExit(1)


def new_message(sender: str, mtype: str, to: list[str], subject: str, body: str, **extra) -> dict:
    ts = now()
    mid = f"{ts.strftime('%Y%m%dT%H%M%S')}-{sender}-{uuid.uuid4().hex[:6]}"
    m = {"id": mid, "ts": iso(ts), "from": sender, "to": to or ["all"], "type": mtype,
         "subject": subject, "body": body}
    m.update({k: v for k, v in extra.items() if v not in (None, [], "")})
    m.setdefault("thread", m.get("re") and None)
    if not m.get("thread"):
        m.pop("thread", None)
    m["requires_ack"] = bool(extra.get("requires_ack", mtype in NEEDS_REPLY or mtype in {"claim", "decision", "answer", "unblock"}))
    return m


def msg_path(m: dict) -> str:
    d = parse_iso(m["ts"])
    return f"messages/{d:%Y/%m/%d}/{m['id']}.json"


def addressed_to(m: dict, agent: str) -> bool:
    return agent in m["to"] or "all" in m["to"]


def thread_root(m: dict, by_id: dict[str, dict]) -> str:
    seen = set()
    cur = m
    while cur.get("re") and cur["re"] in by_id and cur["id"] not in seen:
        seen.add(cur["id"])
        cur = by_id[cur["re"]]
    return cur.get("thread") or cur["id"]


def active_claims(msgs: list[dict]) -> list[dict]:
    released = {m["re"] for m in msgs if m["type"] == "release" and m.get("re")}
    t = now()
    out = []
    for m in msgs:
        if m["type"] != "claim" or m["id"] in released:
            continue
        ttl = int(m.get("ttl_minutes", 120))
        if parse_iso(m["ts"]) + timedelta(minutes=ttl) < t:
            continue
        out.append(m)
    return out


def paths_overlap(a: list[str], b: list[str]) -> bool:
    def norm(p: str) -> str:
        return p.rstrip("/")
    for x in a:
        for y in b:
            x2, y2 = norm(x), norm(y)
            if x2 == y2 or fnmatch.fnmatch(x2, y2) or fnmatch.fnmatch(y2, x2):
                return True
            if x2.startswith(y2.rstrip("*") + "/") or y2.startswith(x2.rstrip("*") + "/"):
                return True
    return False


def print_msgs(ms: list[dict], fmt: str) -> None:
    if fmt == "json":
        for m in ms:
            print(json.dumps(m, ensure_ascii=False))
        return
    if not ms:
        print("(none)")
        return
    for m in ms:
        to = ",".join(m["to"])
        re_ = f" re:{m['re']}" if m.get("re") else ""
        print(f"{m['id']}  [{m['type']}] {m['from']} → {to}{re_}\n    {m['subject']}")
        if m.get("paths"):
            print(f"    paths: {' '.join(m['paths'])}")
        if m.get("body"):
            for line in m["body"].splitlines()[:12]:
                print(f"    | {line}")


# ───────────────────────── commands ─────────────────────────
def cmd_init(L: Ledger, a) -> None:
    L.ensure()
    print(f"ledger ready: {L.remote}/{L.branch} (shared commits go to {L.integration})")


def cmd_hello(L: Ledger, a) -> None:
    info = {"name": a.as_, "role": a.role, "segment": a.segment, "worktree": a.worktree or os.getcwd(),
            "branch": a.branch, "started": iso(now()), "purpose": a.purpose}
    m = new_message(a.as_, "hello", ["all"], f"{a.as_} online: {a.purpose or a.role}", "", requires_ack=False)
    L.write({f"agents/{a.as_}.json": json.dumps(info, indent=1), msg_path(m): json.dumps(m, indent=1)},
            f"hello {a.as_}")
    print(m["id"])


def cmd_post(L: Ledger, a) -> None:
    if a.type not in TYPES:
        sys.exit(f"unknown type {a.type}; one of {sorted(TYPES)}")
    body = Path(a.body_file).read_text() if a.body_file else (a.body or "")
    to = [t for t in (a.to or "all").split(",") if t]
    L.pull()
    thread = None
    if a.re:
        by_id = {m["id"]: m for m in L.messages()}
        parent = by_id.get(a.re) or L.get(a.re)
        thread = thread_root(parent, by_id)
        a.re = parent["id"]
    m = new_message(a.as_, a.type, to, a.subject, body, re=a.re, thread=thread, paths=a.paths,
                    ttl_minutes=a.ttl, refs=_refs(a), requires_ack=a.ack if a.ack is not None else (a.type in NEEDS_REPLY))
    files = {msg_path(m): json.dumps(m, indent=1, ensure_ascii=False)}
    if a.re:  # replying implies acknowledging the parent
        files[f"acks/{a.re}/{a.as_}.json"] = json.dumps({"by": a.as_, "ts": iso(now()), "via": m["id"]})
    L.write(files, f"{a.type} {a.as_}: {a.subject[:60]}")
    print(m["id"])


def _refs(a) -> dict | None:
    refs = {}
    for k in ("commit", "branch", "pr"):
        v = getattr(a, k, None)
        if v:
            refs[k] = v
    return refs or None


def cmd_ack(L: Ledger, a) -> None:
    L.pull()
    files = {}
    for mid in a.ids:
        m = L.get(mid)
        files[f"acks/{m['id']}/{a.as_}.json"] = json.dumps({"by": a.as_, "ts": iso(now()), "note": a.note or ""})
    L.write(files, f"ack {a.as_}: {len(files)}")
    print(f"acked {len(files)}")


def cmd_inbox(L: Ledger, a) -> None:
    L.pull()
    acks = L.acks()
    ms = [m for m in L.messages()
          if addressed_to(m, a.as_) and m["from"] != a.as_ and a.as_ not in acks.get(m["id"], set())
          and (a.all or m.get("requires_ack") or m["type"] in NEEDS_REPLY)]
    if a.type:
        ms = [m for m in ms if m["type"] in a.type.split(",")]
    print_msgs(ms, a.format)


def cmd_show(L: Ledger, a) -> None:
    L.pull()
    m = L.get(a.id)
    acks = sorted(L.acks().get(m["id"], set()))
    replies = [r for r in L.messages() if r.get("re") == m["id"]]
    print_msgs([m], "text")
    print(f"    acked by: {', '.join(acks) or '—'}")
    if replies:
        print("  replies:")
        print_msgs(replies, "text")


def cmd_thread(L: Ledger, a) -> None:
    L.pull()
    msgs = L.messages()
    by_id = {m["id"]: m for m in msgs}
    root = thread_root(L.get(a.id), by_id)
    print_msgs([m for m in msgs if m["id"] == root or thread_root(m, by_id) == root], a.format)


def cmd_claim(L: Ledger, a) -> None:
    L.pull()
    clash = [c for c in active_claims(L.messages()) if c["from"] != a.as_ and paths_overlap(c.get("paths", []), a.paths)]
    if clash and not a.force:
        print("CLAIMED BY OTHERS — coordinate before touching these paths:")
        print_msgs(clash, "text")
        raise SystemExit(3)
    m = new_message(a.as_, "claim", ["all"], a.reason or f"claim {' '.join(a.paths)}", "", paths=a.paths,
                    ttl_minutes=a.ttl, requires_ack=False)
    L.write({msg_path(m): json.dumps(m, indent=1)}, f"claim {a.as_}: {' '.join(a.paths)[:60]}")
    print(m["id"])


def cmd_release(L: Ledger, a) -> None:
    L.pull()
    c = L.get(a.claim_id)
    if c["type"] != "claim":
        sys.exit("not a claim")
    m = new_message(a.as_, "release", ["all"], f"release {' '.join(c.get('paths', []))}", a.note or "", re=c["id"], requires_ack=False)
    L.write({msg_path(m): json.dumps(m, indent=1)}, f"release {a.as_}")
    print(m["id"])


def cmd_claims(L: Ledger, a) -> None:
    L.pull()
    print_msgs(active_claims(L.messages()), a.format)


def cmd_wait(L: Ledger, a) -> None:
    deadline = time.time() + a.timeout
    types = set(a.type.split(",")) if a.type else None
    while True:
        L.pull()
        acks = L.acks()
        for m in L.messages():
            if m["from"] == a.as_ or not addressed_to(m, a.as_):
                continue
            if types and m["type"] not in types:
                continue
            if a.re and m.get("re") != a.re and not m["id"].startswith(a.re):
                continue
            if a.from_ and m["from"] != a.from_:
                continue
            if a.as_ in acks.get(m["id"], set()):
                continue
            print_msgs([m], a.format)
            return
        if time.time() > deadline:
            print("timeout: nothing arrived", file=sys.stderr)
            raise SystemExit(5)
        time.sleep(a.interval)


def cmd_board(L: Ledger, a) -> None:
    L.pull()
    msgs = L.messages()
    acks = L.acks()
    by_id = {m["id"]: m for m in msgs}
    replied = {m["re"] for m in msgs if m.get("re")}
    print("AGENTS")
    for name, info in sorted(L.agents().items()):
        last = max((m["ts"] for m in msgs if m["from"] == name), default="—")
        done = any(m["type"] == "done" and m["from"] == name for m in msgs)
        print(f"  {name:22s} {info.get('role',''):18s} seg={info.get('segment') or '-':6s} last={last[:19]} {'DONE' if done else ''}")
    print("\nOPEN (needs a reply)")
    open_ = [m for m in msgs if m["type"] in NEEDS_REPLY and m["id"] not in replied]
    print_msgs(open_, "text")
    print("\nBLOCKED")
    unblocked = {m["re"] for m in msgs if m["type"] == "unblock" and m.get("re")}
    print_msgs([m for m in msgs if m["type"] == "block" and m["id"] not in unblocked], "text")
    print("\nACTIVE CLAIMS")
    print_msgs(active_claims(msgs), "text")
    print("\nUNACKED (requires_ack, by recipient)")
    agents = set(L.agents())
    for m in msgs:
        if not m.get("requires_ack"):
            continue
        targets = (agents - {m["from"]}) if "all" in m["to"] else set(m["to"])
        missing = sorted(targets - acks.get(m["id"], set()))
        if missing:
            print(f"  {m['id']} [{m['type']}] waiting on: {', '.join(missing)}")
    _ = by_id


def cmd_integrate(L: Ledger, a) -> None:
    """Rebase the current worktree's commits onto origin/<integration> and push. Run inside YOUR worktree."""
    cwd = Path.cwd()
    if git("status", "--porcelain", cwd=cwd).stdout.strip():
        sys.exit("working tree not clean: commit or stash first")
    if a.test_cmd:
        print(f"running tests: {a.test_cmd}")
        if subprocess.run(a.test_cmd, shell=True, cwd=cwd).returncode != 0:
            sys.exit("tests failed: not integrating")
    for attempt in range(6):
        git("fetch", "-q", L.remote, L.integration, cwd=cwd, check=False, quiet=True)
        has_remote = git("rev-parse", "--verify", "-q", f"{L.remote}/{L.integration}", cwd=cwd, check=False).returncode == 0
        if has_remote:
            r = git("rebase", f"{L.remote}/{L.integration}", cwd=cwd, check=False)
            if r.returncode != 0:
                files = git("diff", "--name-only", "--diff-filter=U", cwd=cwd, check=False).stdout.split()
                git("rebase", "--abort", cwd=cwd, check=False)
                authors = git("log", "--format=%s", "-n", "15", f"{L.remote}/{L.integration}", "--", *files, cwd=cwd, check=False).stdout.strip()
                body = f"Rebase onto {L.integration} conflicted in:\n" + "\n".join(f"- {f}" for f in files) + \
                       f"\n\nRecent integration commits touching them:\n{authors}\n\nMy branch is unchanged (rebase aborted)."
                m = new_message(a.as_, "conflict", (a.notify or "all").split(","), f"integration conflict: {', '.join(files)[:80]}",
                                body, paths=files, requires_ack=True)
                L.write({msg_path(m): json.dumps(m, indent=1)}, f"conflict {a.as_}")
                print(f"CONFLICT posted to ledger as {m['id']}; resolve with the owners, then rerun.")
                raise SystemExit(4)
        p = git("push", "-q", L.remote, f"HEAD:refs/heads/{L.integration}", cwd=cwd, check=False)
        if p.returncode == 0:
            sha = git("rev-parse", "--short", "HEAD", cwd=cwd).stdout.strip()
            if not a.quiet:
                m = new_message(a.as_, "status", ["all"], f"integrated {sha} into {L.integration}", a.message or "",
                                refs={"commit": sha, "branch": L.integration}, requires_ack=False)
                L.write({msg_path(m): json.dumps(m, indent=1)}, f"integrated {a.as_} {sha}")
            print(f"integrated {sha}")
            return
        time.sleep(min(20, 2 ** attempt + random.random()))
    sys.exit("push to integration failed after retries")


def cmd_promote(L: Ledger, a) -> None:
    cwd = Path.cwd()
    if git("status", "--porcelain", cwd=cwd).stdout.strip():
        sys.exit("working tree not clean")
    git("fetch", "-q", L.remote, L.integration, a.to, cwd=cwd)
    git("checkout", "-q", a.to, cwd=cwd)
    git("pull", "-q", "--ff-only", L.remote, a.to, cwd=cwd)
    msg = a.message or f"merge {L.integration} into {a.to}"
    r = git("merge", "--no-ff", "-m", msg, f"{L.remote}/{L.integration}", cwd=cwd, check=False)
    if r.returncode != 0:
        git("merge", "--abort", cwd=cwd, check=False)
        sys.exit("merge conflict promoting integration; resolve manually")
    git("push", "-q", L.remote, a.to, cwd=cwd)
    sha = git("rev-parse", "--short", "HEAD", cwd=cwd).stdout.strip()
    m = new_message(a.as_, "status", ["all"], f"promoted {L.integration} → {a.to} at {sha}", a.message or "",
                    refs={"commit": sha, "branch": a.to}, requires_ack=False)
    L.write({msg_path(m): json.dumps(m, indent=1)}, f"promote {sha}")
    print(f"promoted {sha}")



# ───────────────────────── task distributor ─────────────────────────
TASK_STATES = ["assigned", "accepted", "in_progress", "blocked", "done", "verified", "reopened", "declined"]


def task_status(task: dict, msgs: list[dict]) -> dict:
    """Derive a task's state from the messages that reference it (re == task id)."""
    st = {"id": task["id"], "key": task.get("task_key"), "owner": task["to"][0] if task["to"] else "?", "title": task["subject"],
          "deps": task.get("deps", []), "state": "assigned", "last": task["ts"], "note": "", "commits": []}
    for m in msgs:
        if m.get("re") != task["id"] and m.get("task") != task["id"]:
            continue
        st["last"] = m["ts"]
        t = m["type"]
        if t == "accept": st["state"] = "accepted"
        elif t == "decline": st["state"] = "declined"; st["note"] = m["subject"]
        elif t == "update": st["state"] = "in_progress"; st["note"] = m["subject"]
        elif t == "block": st["state"] = "blocked"; st["note"] = m["subject"]
        elif t == "unblock": st["state"] = "in_progress"
        elif t == "done": st["state"] = "done"; st["note"] = m["subject"]
        elif t == "verify": st["state"] = "verified"
        elif t == "reopen": st["state"] = "reopened"; st["note"] = m["subject"]
        if m.get("refs", {}).get("commit"):
            st["commits"].append(m["refs"]["commit"])
    return st


def find_task(L: "Ledger", key: str) -> dict:
    for m in L.messages():
        if m["type"] == "task" and (m["id"] == key or m["id"].startswith(key) or m.get("task_key") == key):
            return m
    sys.stderr.write(f"no task {key}\n")
    raise SystemExit(1)


def cmd_plan(L, a) -> None:
    body = Path(a.body_file).read_text() if a.body_file else (a.body or "")
    m = new_message(a.as_, "plan", ["all"], a.subject, body, requires_ack=True)
    L.write({msg_path(m): json.dumps(m, indent=1, ensure_ascii=False)}, f"plan {a.as_}: {a.subject[:60]}")
    print(m["id"])


def cmd_assign(L, a) -> None:
    L.pull()
    body = Path(a.body_file).read_text() if a.body_file else (a.body or "")
    deps = []
    for d in a.deps or []:
        deps.append(find_task(L, d)["id"])
    m = new_message(a.as_, "task", [a.to], a.subject, body, task_key=a.key, paths=a.paths, deps=deps,
                    acceptance=a.acceptance, requires_ack=True)
    L.write({msg_path(m): json.dumps(m, indent=1, ensure_ascii=False)}, f"task {a.key or ''} -> {a.to}: {a.subject[:50]}")
    print(m["id"])


def _task_reply(L, a, mtype: str, subject: str) -> None:
    L.pull()
    t = find_task(L, a.task)
    body = Path(a.body_file).read_text() if getattr(a, "body_file", None) else (getattr(a, "body", None) or "")
    to = sorted({t["from"], *(x for x in (getattr(a, "to", None) or "").split(",") if x)})
    m = new_message(a.as_, mtype, to, subject or f"{mtype} {t.get('task_key') or t['id']}", body, re=t["id"],
                    thread=t["id"], refs=_refs(a), requires_ack=mtype in {"done", "decline", "reopen"})
    files = {msg_path(m): json.dumps(m, indent=1, ensure_ascii=False)}
    if mtype in {"accept", "decline"}:
        files[f"acks/{t['id']}/{a.as_}.json"] = json.dumps({"by": a.as_, "ts": iso(now()), "via": m["id"]})
    L.write(files, f"{mtype} {a.as_}: {(t.get('task_key') or t['id'])}")
    print(m["id"])


def cmd_accept(L, a): _task_reply(L, a, "accept", a.subject)
def cmd_decline(L, a): _task_reply(L, a, "decline", a.subject or "declined")
def cmd_update(L, a): _task_reply(L, a, "update", a.subject)
def cmd_done(L, a): _task_reply(L, a, "done", a.subject or "done")
def cmd_verify(L, a): _task_reply(L, a, "reopen" if a.reopen else "verify", a.subject)


def cmd_tasks(L, a) -> None:
    L.pull()
    msgs = L.messages()
    tasks = [m for m in msgs if m["type"] == "task"]
    rows = [task_status(t, msgs) for t in tasks]
    state_by_id = {r["id"]: r["state"] for r in rows}
    if a.format == "json":
        for r in rows:
            print(json.dumps(r))
        return
    if not rows:
        print("(no tasks)")
        return
    for r in rows:
        waiting = [d for d in r["deps"] if state_by_id.get(d) not in ("done", "verified")]
        dep = f" waits-on:{','.join(w[-6:] for w in waiting)}" if waiting else ""
        print(f"{(r['key'] or r['id'][-6:]):10s} {r['state']:11s} {r['owner']:18s} {r['title'][:50]}{dep}")
        if r["note"]:
            print(f"{'':10s} └ {r['note'][:90]}")


LEDGER_README = """# Agent ledger

Append-only coordination log for parallel agents. **Never edit or delete files here**; only add.
Use the CLI: `python .claude/skills/multiplayer/scripts/multiplayer.py --help`.

- `messages/YYYY/MM/DD/<id>.json` — one message per file (claim, contract, decision, question, answer, block,
  unblock, conflict, handoff, status, review-request, done, nudge, note, hello, release)
- `acks/<message-id>/<agent>.json` — one acknowledgement per agent per message
- `agents/<name>.json` — who is working, on what, where

- tasks: `task` messages from the lead; state comes from accept/update/block/done/verify replies

Shared code goes to the shared commit branch `{integration}`; the lead promotes it after review.
"""


def main() -> None:
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # piping into head/grep -q must not traceback
    ap = argparse.ArgumentParser(prog="multiplayer.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--remote", default=DEFAULTS["remote"])
    ap.add_argument("--ledger-branch", default=DEFAULTS["ledger_branch"])
    ap.add_argument("--integration-branch", default=DEFAULTS["integration_branch"])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def p(name, fn, **kw):
        sp = sub.add_parser(name, **kw)
        sp.set_defaults(fn=fn)
        return sp

    def who(sp, required=True):
        sp.add_argument("--as", dest="as_", required=required, help="your agent name, e.g. seg02-db")

    def fmt(sp):
        sp.add_argument("--format", choices=["text", "json"], default="text")

    p("init", cmd_init)
    sp = p("plan", cmd_plan); who(sp); sp.add_argument("--subject", required=True); sp.add_argument("--body"); sp.add_argument("--body-file")
    sp = p("assign", cmd_assign); who(sp); sp.add_argument("--to", required=True); sp.add_argument("--key", help="short task key, e.g. S02")
    sp.add_argument("--subject", required=True); sp.add_argument("--body"); sp.add_argument("--body-file")
    sp.add_argument("--acceptance", help="how 'done' is judged"); sp.add_argument("--paths", nargs="*"); sp.add_argument("--deps", nargs="*")
    for name, fn in (("accept", cmd_accept), ("decline", cmd_decline), ("update", cmd_update), ("done", cmd_done), ("verify", cmd_verify)):
        sp = p(name, fn); who(sp); sp.add_argument("task"); sp.add_argument("--subject"); sp.add_argument("--body"); sp.add_argument("--body-file")
        sp.add_argument("--to", help="extra recipients"); sp.add_argument("--commit"); sp.add_argument("--branch"); sp.add_argument("--pr")
        if name == "verify":
            sp.add_argument("--reopen", action="store_true", help="reject and reopen instead of verifying")
    sp = p("tasks", cmd_tasks); fmt(sp)
    sp = p("hello", cmd_hello); who(sp)
    sp.add_argument("--role", default="implementer"); sp.add_argument("--segment"); sp.add_argument("--worktree")
    sp.add_argument("--branch"); sp.add_argument("--purpose", default="")
    sp = p("post", cmd_post); who(sp)
    sp.add_argument("--type", required=True); sp.add_argument("--to", help="comma list of agents, or 'all'")
    sp.add_argument("--subject", required=True); sp.add_argument("--body"); sp.add_argument("--body-file")
    sp.add_argument("--re", help="message id this replies to"); sp.add_argument("--paths", nargs="*")
    sp.add_argument("--ttl", type=int); sp.add_argument("--commit"); sp.add_argument("--branch"); sp.add_argument("--pr")
    sp.add_argument("--ack", dest="ack", action="store_true", default=None, help="force requires_ack")
    sp.add_argument("--no-ack", dest="ack", action="store_false")
    sp = p("ack", cmd_ack); who(sp); sp.add_argument("ids", nargs="+"); sp.add_argument("--note")
    sp = p("inbox", cmd_inbox); who(sp); fmt(sp); sp.add_argument("--type"); sp.add_argument("--all", action="store_true",
                                                                                            help="include FYI messages too")
    sp = p("show", cmd_show); sp.add_argument("id")
    sp = p("thread", cmd_thread); sp.add_argument("id"); fmt(sp)
    sp = p("claim", cmd_claim); who(sp); sp.add_argument("--paths", nargs="+", required=True)
    sp.add_argument("--ttl", type=int, default=120); sp.add_argument("--reason"); sp.add_argument("--force", action="store_true")
    sp = p("release", cmd_release); who(sp); sp.add_argument("claim_id"); sp.add_argument("--note")
    sp = p("claims", cmd_claims); fmt(sp)
    sp = p("wait", cmd_wait); who(sp); fmt(sp); sp.add_argument("--type"); sp.add_argument("--re"); sp.add_argument("--from", dest="from_")
    sp.add_argument("--timeout", type=int, default=1800); sp.add_argument("--interval", type=int, default=45)
    p("board", cmd_board)
    sp = p("integrate", cmd_integrate); who(sp); sp.add_argument("--test-cmd"); sp.add_argument("--message")
    sp.add_argument("--notify", help="who to address a conflict to (default all)"); sp.add_argument("--quiet", action="store_true")
    sp = p("promote", cmd_promote); who(sp); sp.add_argument("--to", required=True); sp.add_argument("--message")

    a = ap.parse_args()
    if not hasattr(a, "as_"):
        a.as_ = None
    if getattr(a, "as_", None) and not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,40}", a.as_):
        sys.exit("--as must be lowercase letters, digits, . _ - (e.g. seg02-db)")
    L = Ledger(a)
    try:
        a.fn(L, a)
    finally:
        L.cleanup()


if __name__ == "__main__":
    main()
