"""Periodic housekeeping, run by one worker at a time (Postgres advisory lock):

* Abandoned uploads: clips still ``uploading`` after ``abandoned_upload_hours`` are deleted
  with their stored files.
* Retention: original uploads are deleted ``raw_retention_days_after_approval`` days after
  approval (normalized video, tracers and renders are kept). Default from the open questions:
  originals 30 days after approval, renders indefinitely.
"""

from __future__ import annotations

import logging

from . import storage
from .db import Conn
from .storage import Storage, StorageError

logger = logging.getLogger(__name__)
LOCK_KEY = 8_431_771  # arbitrary, stable advisory-lock key for maintenance


def try_lock(conn: Conn) -> bool:
    row = conn.execute("select pg_try_advisory_lock(%s) as ok", (LOCK_KEY,)).fetchone()
    return bool(row and row["ok"])


def unlock(conn: Conn) -> None:
    conn.execute("select pg_advisory_unlock(%s)", (LOCK_KEY,))


def delete_abandoned_uploads(conn: Conn, store: Storage, hours: int, limit: int = 200) -> int:
    rows = conn.execute(
        """
        select id, user_id, raw_path from public.clips
         where status = 'uploading' and created_at < now() - make_interval(hours => %s)
         order by created_at limit %s
        """,
        (hours, limit),
    ).fetchall()
    removed = 0
    for r in rows:
        try:
            if r["raw_path"]:
                store.delete(storage.RAW, [r["raw_path"]])
        except StorageError:
            logger.warning("could not delete abandoned upload file", extra={"clip_id": str(r["id"])})
            continue
        with conn.transaction():
            conn.execute("delete from public.clips where id = %s and status = 'uploading'", (r["id"],))
        removed += 1
    return removed


def expire_originals(conn: Conn, store: Storage, days: int, limit: int = 200) -> int:
    rows = conn.execute(
        """
        select id, raw_path from public.clips
         where raw_path is not null and approved_at is not null
           and approved_at < now() - make_interval(days => %s)
         order by approved_at limit %s
        """,
        (days, limit),
    ).fetchall()
    removed = 0
    for r in rows:
        try:
            store.delete(storage.RAW, [r["raw_path"]])
        except StorageError:
            logger.warning("could not delete original", extra={"clip_id": str(r["id"])})
            continue
        with conn.transaction():
            conn.execute("update public.clips set raw_path = null where id = %s", (r["id"],))
        removed += 1
    return removed


def run(conn: Conn, store: Storage, *, abandoned_hours: int, retention_days: int) -> dict[str, int] | None:
    """Run every task if this process wins the lock; None when another worker holds it."""
    if not try_lock(conn):
        return None
    try:
        result = {
            "abandoned_uploads": delete_abandoned_uploads(conn, store, abandoned_hours),
            "expired_originals": expire_originals(conn, store, retention_days),
        }
    finally:
        unlock(conn)
    if any(result.values()):
        logger.info("maintenance", extra=result)
    return result
