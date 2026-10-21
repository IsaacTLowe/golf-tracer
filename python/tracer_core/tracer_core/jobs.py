"""Postgres-backed job queue (table ``public.jobs``).

Workers claim the oldest runnable job with ``FOR UPDATE SKIP LOCKED``, heartbeat ``locked_at``
while running, and either succeed, retry with exponential backoff, or fail permanently.
Locks older than the lock timeout belong to dead workers and are reclaimed.
"""

from __future__ import annotations

from typing import Any, Literal

from .db import Conn, Row, jsonb

JobKind = Literal["process", "retrace", "render"]
NOTIFY_CHANNEL = "jobs_queued"
BACKOFF_BASE_S = 15


def enqueue(
    conn: Conn,
    clip_id: str,
    kind: JobKind,
    payload: dict[str, Any] | None = None,
    *,
    max_attempts: int = 3,
) -> Row:
    """Queue a job. An already-queued job of the same kind for the clip is replaced, so
    double-clicks and retries don't pile up work."""
    conn.execute(
        "delete from public.jobs where clip_id = %s and kind = %s and status = 'queued'",
        (clip_id, kind),
    )
    row = conn.execute(
        """
        insert into public.jobs (clip_id, kind, payload, max_attempts)
        values (%s, %s, %s, %s)
        returning *
        """,
        (clip_id, kind, jsonb(payload or {}), max_attempts),
    ).fetchone()
    assert row is not None
    return row


def claim(conn: Conn, worker_id: str) -> Row | None:
    """Claim the oldest runnable job, or None. Commits so the lock is visible to others."""
    with conn.transaction():
        row = conn.execute(
            """
            with next_job as (
              select id from public.jobs
              where status = 'queued' and run_after <= now()
              order by created_at, id
              for update skip locked
              limit 1
            )
            update public.jobs j
               set status = 'running', locked_at = now(), locked_by = %s, attempts = j.attempts + 1
              from next_job
             where j.id = next_job.id
            returning j.*
            """,
            (worker_id,),
        ).fetchone()
    return row


def heartbeat(conn: Conn, job_id: str, worker_id: str) -> bool:
    with conn.transaction():
        cur = conn.execute(
            "update public.jobs set locked_at = now() where id = %s and locked_by = %s and status = 'running'",
            (job_id, worker_id),
        )
    return cur.rowcount == 1


def succeed(conn: Conn, job_id: str) -> None:
    conn.execute(
        "update public.jobs set status = 'succeeded', finished_at = now(), last_error = null where id = %s",
        (job_id,),
    )


def backoff_seconds(attempts: int) -> int:
    return int(BACKOFF_BASE_S * 2 ** max(attempts - 1, 0))


def fail(conn: Conn, job: Row, error: str, *, retryable: bool = True) -> bool:
    """Record a failure. Returns True if the job will be retried, False if it failed for good."""
    will_retry = retryable and job["attempts"] < job["max_attempts"]
    if will_retry:
        conn.execute(
            """
            update public.jobs
               set status = 'queued', locked_at = null, locked_by = null, last_error = %s,
                   run_after = now() + make_interval(secs => %s)
             where id = %s
            """,
            (error[:2000], backoff_seconds(job["attempts"]), job["id"]),
        )
    else:
        conn.execute(
            """
            update public.jobs
               set status = 'failed', finished_at = now(), locked_at = null, last_error = %s
             where id = %s
            """,
            (error[:2000], job["id"]),
        )
    return will_retry


def reclaim_stale(conn: Conn, lock_timeout_s: int) -> list[Row]:
    """Requeue running jobs whose worker stopped heartbeating; fail those out of attempts.

    Returns the jobs that failed permanently so the caller can mark their clips failed.
    """
    with conn.transaction():
        conn.execute(
            """
            update public.jobs
               set status = 'queued', locked_at = null, locked_by = null,
                   last_error = 'lock expired (worker stopped responding)', run_after = now()
             where status = 'running' and locked_at < now() - make_interval(secs => %s)
               and attempts < max_attempts
            """,
            (lock_timeout_s,),
        )
        failed = conn.execute(
            """
            update public.jobs
               set status = 'failed', finished_at = now(), locked_at = null,
                   last_error = 'lock expired (worker stopped responding)'
             where status = 'running' and locked_at < now() - make_interval(secs => %s)
               and attempts >= max_attempts
            returning *
            """,
            (lock_timeout_s,),
        ).fetchall()
    return failed


def active_job(conn: Conn, clip_id: str, kinds: tuple[JobKind, ...]) -> Row | None:
    return conn.execute(
        """
        select * from public.jobs
         where clip_id = %s and kind = any(%s::public.job_kind[]) and status in ('queued', 'running')
         order by created_at desc limit 1
        """,
        (clip_id, list(kinds)),
    ).fetchone()
