from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest
from tracer_core import db, jobs

pytestmark = pytest.mark.db


def _clip(user_id: str) -> str:
    with db.transaction() as conn:
        row = conn.execute(
            "insert into public.clips (user_id, original_filename, status) values (%s, 'a.mp4', 'queued') returning id",
            (user_id,),
        ).fetchone()
    assert row is not None
    return str(row["id"])


@pytest.fixture
def clip_id(make_user: Any) -> str:
    return _clip(make_user()["id"])


def test_claim_takes_oldest_and_marks_running(clip_id: str, database_url: str) -> None:
    with db.transaction() as conn:
        first = jobs.enqueue(conn, clip_id, "process")
        jobs.enqueue(conn, clip_id, "render", {"tracer_version": 1})
    with db.connect(database_url) as conn:
        job = jobs.claim(conn, "w1")
    assert job is not None
    assert job["id"] == first["id"]
    assert job["status"] == "running" and job["locked_by"] == "w1" and job["attempts"] == 1


def test_enqueue_replaces_queued_duplicate(clip_id: str) -> None:
    with db.transaction() as conn:
        jobs.enqueue(conn, clip_id, "retrace", {"a": 1})
        jobs.enqueue(conn, clip_id, "retrace", {"a": 2})
        rows = conn.execute("select payload from public.jobs where clip_id = %s", (clip_id,)).fetchall()
    assert rows == [{"payload": {"a": 2}}]


def test_concurrent_claims_never_share_a_job(make_user: Any, database_url: str) -> None:
    user = make_user()
    with db.transaction() as conn:
        for _ in range(20):
            jobs.enqueue(conn, _clip(user["id"]), "process")
    claimed: list[str] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        with db.connect(database_url) as conn:
            while True:
                job = jobs.claim(conn, name)
                if job is None:
                    return
                with lock:
                    claimed.append(str(job["id"]))

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(claimed) == 20
    assert len(set(claimed)) == 20


def test_skip_locked_lets_a_second_worker_proceed(make_user: Any, database_url: str) -> None:
    user = make_user()
    with db.transaction() as conn:
        a = jobs.enqueue(conn, _clip(user["id"]), "process")
        b = jobs.enqueue(conn, _clip(user["id"]), "process")
    # Worker 1 holds a row lock on job A mid-claim (open transaction).
    holder = db.connect(database_url)
    try:
        holder.execute("select id from public.jobs where id = %s for update", (a["id"],))
        with db.connect(database_url) as conn:
            job = jobs.claim(conn, "w2")
        assert job is not None and job["id"] == b["id"]
    finally:
        holder.rollback()
        holder.close()


def test_failure_backs_off_then_fails_for_good(clip_id: str, database_url: str) -> None:
    with db.transaction() as conn:
        jobs.enqueue(conn, clip_id, "process", max_attempts=2)
    with db.connect(database_url) as conn:
        job = jobs.claim(conn, "w1")
        assert job is not None
        with conn.transaction():
            assert jobs.fail(conn, job, "boom") is True
        row = conn.execute(
            "select status, run_after > now() as later, last_error from public.jobs where id = %s", (job["id"],)
        ).fetchone()
        assert row == {"status": "queued", "later": True, "last_error": "boom"}
        assert jobs.claim(conn, "w1") is None  # not runnable until run_after
        with conn.transaction():
            conn.execute("update public.jobs set run_after = now() where id = %s", (job["id"],))
        job = jobs.claim(conn, "w1")
        assert job is not None and job["attempts"] == 2
        with conn.transaction():
            assert jobs.fail(conn, job, "boom again") is False
        row = conn.execute(
            "select status, finished_at is not null as done from public.jobs where id = %s", (job["id"],)
        ).fetchone()
        assert row == {"status": "failed", "done": True}


def test_non_retryable_failure_is_final(clip_id: str, database_url: str) -> None:
    with db.transaction() as conn:
        jobs.enqueue(conn, clip_id, "process")
    with db.connect(database_url) as conn:
        job = jobs.claim(conn, "w1")
        assert job is not None
        with conn.transaction():
            assert jobs.fail(conn, job, "bad file", retryable=False) is False


def test_backoff_is_exponential() -> None:
    assert [jobs.backoff_seconds(a) for a in (1, 2, 3)] == [15, 30, 60]


def test_stale_locks_are_reclaimed(make_user: Any, database_url: str) -> None:
    user = make_user()
    with db.transaction() as conn:
        a = jobs.enqueue(conn, _clip(user["id"]), "process")
        b = jobs.enqueue(conn, _clip(user["id"]), "process", max_attempts=1)
    with db.connect(database_url) as conn:
        assert jobs.claim(conn, "dead-worker") is not None
        assert jobs.claim(conn, "dead-worker") is not None
        with conn.transaction():
            conn.execute("update public.jobs set locked_at = now() - interval '11 minutes'")
        failed = jobs.reclaim_stale(conn, 600)
        assert [str(f["id"]) for f in failed] == [str(b["id"])]
        rows = {str(r["id"]): r["status"] for r in conn.execute("select id, status from public.jobs")}
        assert rows == {str(a["id"]): "queued", str(b["id"]): "failed"}


def test_heartbeat_keeps_lock_fresh(clip_id: str, database_url: str) -> None:
    with db.transaction() as conn:
        jobs.enqueue(conn, clip_id, "process")
    with db.connect(database_url) as conn:
        job = jobs.claim(conn, "w1")
        assert job is not None
        with conn.transaction():
            conn.execute("update public.jobs set locked_at = now() - interval '11 minutes'")
        assert jobs.heartbeat(conn, str(job["id"]), "w1") is True
        assert jobs.heartbeat(conn, str(job["id"]), "someone-else") is False
        assert jobs.reclaim_stale(conn, 600) == []
        row = conn.execute("select status from public.jobs where id = %s", (job["id"],)).fetchone()
        assert row == {"status": "running"}


def test_notify_fires_on_enqueue(clip_id: str, database_url: str) -> None:
    with db.connect(database_url, autocommit=True) as listen:
        listen.execute(f"listen {jobs.NOTIFY_CHANNEL}")
        with db.transaction() as conn:
            jobs.enqueue(conn, clip_id, "process")
        got = [n.payload for n in listen.notifies(timeout=2, stop_after=1)]
    assert got == ["process"]


def test_unknown_clip_id_shape_is_rejected_by_db(make_user: Any) -> None:
    import psycopg

    with pytest.raises(psycopg.errors.ForeignKeyViolation), db.transaction() as conn:
        jobs.enqueue(conn, str(uuid.uuid4()), "process")


def test_maintenance_removes_abandoned_uploads_and_old_originals(
    make_user: Any, local_storage: Any, database_url: str
) -> None:
    from tracer_core import maintenance

    user = make_user()
    with db.transaction() as conn:
        old = conn.execute(
            "insert into public.clips (user_id, original_filename, status, raw_path, created_at)"
            " values (%s, 'a.mp4', 'uploading', %s, now() - interval '25 hours') returning id",
            (user["id"], f"{user['id']}/x/original.mp4"),
        ).fetchone()
        fresh = conn.execute(
            "insert into public.clips (user_id, original_filename, status)"
            " values (%s, 'b.mp4', 'uploading') returning id",
            (user["id"],),
        ).fetchone()
        approved = conn.execute(
            "insert into public.clips (user_id, original_filename, status, raw_path, approved_at)"
            " values (%s, 'c.mp4', 'approved', %s, now() - interval '31 days') returning id",
            (user["id"], f"{user['id']}/y/original.mp4"),
        ).fetchone()
    for name in ("x", "y"):
        p = local_storage.root / "raw" / user["id"] / name / "original.mp4"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"1")
    with db.connect(database_url, autocommit=True) as conn:
        result = maintenance.run(conn, local_storage, abandoned_hours=24, retention_days=30)
    assert result == {"abandoned_uploads": 1, "expired_originals": 1}
    with db.transaction() as conn:
        ids = {str(r["id"]): r["raw_path"] for r in conn.execute("select id, raw_path from public.clips")}
    assert str(old["id"]) not in ids
    assert str(fresh["id"]) in ids
    assert ids[str(approved["id"])] is None
    assert not (local_storage.root / "raw" / user["id"] / "x" / "original.mp4").exists()

