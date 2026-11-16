"""Run the real top-level app (`naadi_api.agent.host:app`, Aegra + Naadi) in a uvicorn subprocess against
the local test database, with throwaway HS256 tokens for test users. Used by the agent integration tests."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import jwt
import psycopg

API_DIR = Path(__file__).resolve().parents[2]
# Throwaway, local-only signing secret for the test server (never a real project's secret).
TEST_JWT_SECRET = "naadi-agent-host-test-secret-0000000000"
TEST_SUPABASE_URL = "http://127.0.0.1:9"
ADMIN_EMAIL = "agent-admin@test.invalid"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def token(user_id: uuid.UUID, email: str, *, ttl_s: int = 3600) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": f"{TEST_SUPABASE_URL}/auth/v1",
            "aud": "authenticated",
            "sub": str(user_id),
            "role": "authenticated",
            "iat": now,
            "exp": now + ttl_s,
            "email": email,
            "user_metadata": {"email_verified": True},
            "is_anonymous": False,
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )


@dataclass
class Host:
    url: str
    admin_id: uuid.UUID
    student_id: uuid.UUID
    other_id: uuid.UUID
    admin_token: str
    student_token: str
    other_token: str
    log_path: Path


def _env(database_url: str, port: int) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SUPABASE_", "VITE_SUPABASE_"))}
    env.update(
        {
            "DATABASE_URL": database_url,
            "SUPABASE_URL": TEST_SUPABASE_URL,
            "SUPABASE_JWT_SECRET": TEST_JWT_SECRET,
            "ADMIN_EMAILS": ADMIN_EMAIL,
            "APP_ENV": "local",
            "CRON_POLL_INTERVAL_SECONDS": "2",
            "NAADI_STATIC_DIR": str(API_DIR / "tests" / "agent" / "no-static"),
            "PORT": str(port),
        }
    )
    env.setdefault("NVIDIA_API_KEY", "nvapi-proxy-injected")
    return env


@contextmanager
def running_host(database_url: str, log_dir: Path) -> Iterator[Host]:
    from naadi_api.agent import migrate_all
    from naadi_api.settings import Settings

    assert migrate_all.run(Settings(database_url=database_url)) == 0
    with psycopg.connect(database_url, autocommit=True) as conn:
        ids = [uuid.uuid4() for _ in range(3)]
        emails = [ADMIN_EMAIL, f"{ids[1].hex[:10]}@test.invalid", f"{ids[2].hex[:10]}@test.invalid"]
        conn.execute("delete from auth.users where email = %s", (ADMIN_EMAIL,))
        for uid, email in zip(ids, emails, strict=True):
            conn.execute(
                "insert into auth.users (id, email, aud, role) values (%s, %s, 'authenticated', 'authenticated')",
                (uid, email),
            )
    port = free_port()
    log_path = log_dir / f"agent-host-{port}.log"
    with log_path.open("w") as log:
        proc = subprocess.Popen(  # noqa: S603 - fixed argv
            [
                sys.executable,
                "-m",
                "uvicorn",
                "naadi_api.agent.host:app",
                "--port",
                str(port),
                "--workers",
                "1",
                "--no-access-log",
            ],
            cwd=API_DIR,
            env=_env(database_url, port),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"agent host exited early; see {log_path}")
            try:
                if httpx.get(f"{url}/info", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(f"agent host did not start; see {log_path}")
            time.sleep(0.5)
        yield Host(
            url,
            ids[0],
            ids[1],
            ids[2],
            token(ids[0], emails[0]),
            token(ids[1], emails[1]),
            token(ids[2], emails[2]),
            log_path,
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute("delete from auth.users where id = any(%s)", (ids,))
