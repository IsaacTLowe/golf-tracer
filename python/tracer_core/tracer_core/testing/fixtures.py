"""Pytest fixtures shared by the API and worker test suites (loaded from the root conftest).

Database tests need a Postgres server: set TEST_DATABASE_URL (default
postgresql://postgres:postgres@127.0.0.1:54329/postgres). Each test session creates a fresh
database, applies the Supabase stub plus every migration in supabase/migrations, and drops it
at the end.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from tracer_core import db
from tracer_core.config import Settings
from tracer_core.storage import LocalStorage

REPO = Path(__file__).resolve().parents[4]
MIGRATIONS = REPO / "supabase" / "migrations"
STUB = Path(__file__).with_name("stub_supabase.sql")
DEFAULT_URL = "postgresql://postgres:postgres@127.0.0.1:54329/postgres"
AUDIENCE = "authenticated"


def _with_db(url: str, name: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{name}"


def apply_migrations(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(STUB.read_text(encoding="utf-8"))  # type: ignore[arg-type]
        for f in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(f.read_text(encoding="utf-8"))  # type: ignore[arg-type]


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    admin_url = os.environ.get("TEST_DATABASE_URL", DEFAULT_URL)
    name = f"tracer_test_{uuid.uuid4().hex[:8]}"
    try:
        admin = psycopg.connect(admin_url, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as e:
        pytest.skip(f"no test Postgres at {admin_url}: {e}")
    with admin:
        admin.execute(f'create database "{name}"')  # type: ignore[arg-type]
    url = _with_db(admin_url, name)
    try:
        apply_migrations(url)
        yield url
    finally:
        db.close_pool()
        with psycopg.connect(admin_url, autocommit=True) as admin2:
            admin2.execute(f'drop database if exists "{name}" with (force)')  # type: ignore[arg-type]


@pytest.fixture(scope="session")
def pool(database_url: str) -> Iterator[None]:
    db.open_pool(database_url, min_size=1, max_size=8)
    yield
    db.close_pool()


@pytest.fixture
def clean_db(pool: None) -> Iterator[None]:
    with db.transaction() as conn:
        conn.execute(
            "truncate public.jobs, public.tracers, public.clips, public.upload_batches, public.profiles cascade"
        )
        conn.execute("delete from auth.users")
    yield


@pytest.fixture(scope="session")
def signing_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(scope="session")
def verifier(signing_key: ec.EllipticCurvePrivateKey) -> Any:
    from tracer_api.auth import TokenVerifier

    public = signing_key.public_key()
    return TokenVerifier(jwks_url=None, audience=AUDIENCE, key_resolver=lambda _token: public)


@pytest.fixture
def make_token(signing_key: ec.EllipticCurvePrivateKey) -> Callable[..., str]:
    def _make(sub: str, *, aud: str = AUDIENCE, exp_in: int = 3600, email: str | None = None) -> str:
        now = int(time.time())
        claims = {"sub": sub, "aud": aud, "exp": now + exp_in, "iat": now, "role": "authenticated", "email": email}
        return jwt.encode(claims, signing_key, algorithm="ES256", headers={"kid": "test"})

    return _make


@pytest.fixture
def make_user(clean_db: None, make_token: Callable[..., str]) -> Callable[[], dict[str, str]]:
    def _make() -> dict[str, str]:
        uid = str(uuid.uuid4())
        email = f"{uid[:8]}@example.com"
        with db.transaction() as conn:
            conn.execute("insert into auth.users (id, email) values (%s, %s)", (uid, email))
        token = make_token(uid, email=email)
        return {"id": uid, "email": email, "token": token, "auth": f"Bearer {token}"}

    return _make


@pytest.fixture
def local_storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        supabase_url="http://supabase.test",
        supabase_service_role_key="test",
        clip_rate_limit_per_hour=200,
    )


@pytest.fixture
def api(settings: Settings, local_storage: LocalStorage, verifier: Any, pool: None) -> Iterator[Any]:
    from fastapi.testclient import TestClient
    from tracer_api.main import create_app

    app = create_app(settings, storage=local_storage, verifier=verifier, manage_pool=False)
    with TestClient(app) as client:
        yield client
