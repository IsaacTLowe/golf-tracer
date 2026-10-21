"""Versioned tracer storage (table ``public.tracers``)."""

from __future__ import annotations

from typing import Any, Literal

from .db import Conn, Row, jsonb

Source = Literal["auto", "manual", "edited"]


def current(conn: Conn, clip_id: str) -> Row | None:
    return conn.execute("select * from public.tracers where clip_id = %s and is_current", (clip_id,)).fetchone()


def insert_version(
    conn: Conn, clip_id: str, source: Source, data: dict[str, Any], confidence: float | None = None
) -> Row:
    """Append a new version and make it current. Call inside a transaction holding the clip row lock."""
    conn.execute("update public.tracers set is_current = false where clip_id = %s and is_current", (clip_id,))
    row = conn.execute(
        """
        insert into public.tracers (clip_id, version, source, data, confidence, is_current)
        values (
          %s,
          coalesce((select max(version) from public.tracers where clip_id = %s), 0) + 1,
          %s, %s, %s, true
        )
        returning *
        """,
        (clip_id, clip_id, source, jsonb(data), None if confidence is None else round(float(confidence), 3)),
    ).fetchone()
    assert row is not None
    return row


def versions(conn: Conn, clip_id: str) -> list[Row]:
    return conn.execute("select * from public.tracers where clip_id = %s order by version desc", (clip_id,)).fetchall()
