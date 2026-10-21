"""Postgres access. The API and worker connect directly (not through the REST layer) using a
direct or session-mode connection so LISTEN/NOTIFY and FOR UPDATE SKIP LOCKED work."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

Row = dict[str, Any]
Conn = psycopg.Connection[Row]

_pool: ConnectionPool[Conn] | None = None


def open_pool(database_url: str, *, min_size: int = 1, max_size: int = 10) -> ConnectionPool[Conn]:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            database_url,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row, "autocommit": False, "prepare_threshold": None},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def pool() -> ConnectionPool[Conn]:
    if _pool is None:
        raise RuntimeError("database pool is not open")
    return _pool


@contextmanager
def transaction() -> Iterator[Conn]:
    """A pooled connection inside one transaction (commit on success, rollback on error)."""
    with pool().connection() as conn, conn.transaction():
        yield conn


def connect(database_url: str, *, autocommit: bool = False) -> Conn:
    return psycopg.connect(database_url, row_factory=dict_row, autocommit=autocommit, prepare_threshold=None)


def jsonb(value: Any) -> Jsonb:
    return Jsonb(value)
