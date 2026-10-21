"""Worker entrypoint: claim jobs from Postgres and run them, one at a time per process.

Scale by running more replicas. Idle workers block on LISTEN jobs_queued (with a poll
timeout as a safety net), a heartbeat thread keeps ``locked_at`` fresh while a job runs,
and stale locks from dead workers are reclaimed periodically.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import traceback
from typing import Any

import psycopg
from tracer_core import db, jobs, maintenance
from tracer_core import log as tlog
from tracer_core.config import Settings, get_settings
from tracer_core.storage import Storage, from_settings

from .handlers import HANDLERS, ClipGone, Context, JobError, mark_failed

logger = logging.getLogger("tracer_worker")
RECLAIM_EVERY_S = 60
MAINTENANCE_EVERY_S = 3600


class Heartbeat:
    def __init__(self, database_url: str, job_id: str, worker_id: str, interval: float):
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(database_url, job_id, worker_id, interval), daemon=True)

    def _run(self, url: str, job_id: str, worker_id: str, interval: float) -> None:
        try:
            with db.connect(url) as conn:
                while not self.stop.wait(interval):
                    jobs.heartbeat(conn, job_id, worker_id)
        except Exception:
            logger.exception("heartbeat failed", extra={"job_id": job_id})

    def __enter__(self) -> Heartbeat:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop.set()
        self.thread.join(timeout=5)


class Worker:
    def __init__(self, settings: Settings, store: Storage, worker_id: str | None = None):
        self.settings = settings
        self.worker_id = worker_id or settings.worker_id or f"{socket.gethostname()}-{os.getpid()}"
        self.ctx = Context(settings=settings, storage=store, connect=lambda: db.connect(settings.database_url))
        self.stopping = False
        self._last_reclaim = 0.0
        self._last_maintenance = 0.0

    def run_one(self, conn: db.Conn) -> bool:
        """Claim and run one job. Returns False when the queue was empty."""
        job = jobs.claim(conn, self.worker_id)
        if job is None:
            return False
        log = {
            "job_id": str(job["id"]),
            "clip_id": str(job["clip_id"]),
            "kind": job["kind"],
            "attempt": job["attempts"],
        }
        logger.info("job started", extra=log)
        started = time.perf_counter()
        handler = HANDLERS[job["kind"]]
        try:
            with Heartbeat(self.settings.database_url, str(job["id"]), self.worker_id, self.settings.job_heartbeat_s):
                handler(self.ctx, job)
        except ClipGone:
            with conn.transaction():
                jobs.succeed(conn, str(job["id"]))
            logger.info("clip deleted during job", extra=log)
            return True
        except JobError as e:
            self._failed(conn, job, e.code, str(e), e.retryable, log)
            return True
        except Exception as e:  # unexpected: retry with backoff
            logger.error("job crashed", extra={**log, "exc": traceback.format_exc()})
            self._failed(conn, job, "internal_error", f"{type(e).__name__}: {e}", True, log)
            return True
        with conn.transaction():
            jobs.succeed(conn, str(job["id"]))
        logger.info("job succeeded", extra={**log, "seconds": round(time.perf_counter() - started, 2)})
        return True

    def _failed(
        self, conn: db.Conn, job: dict[str, Any], code: str, message: str, retryable: bool, log: dict[str, Any]
    ) -> None:
        with conn.transaction():
            will_retry = jobs.fail(conn, job, f"{code}: {message}", retryable=retryable)
        if will_retry:
            logger.warning("job failed, will retry", extra={**log, "code": code, "error": message[:300]})
            # Leave the clip visibly queued while it waits for the retry.
            with conn.transaction():
                conn.execute(
                    "update public.clips set status = 'queued' where id = %s and status = 'processing'",
                    (job["clip_id"],),
                )
        else:
            logger.error("job failed permanently", extra={**log, "code": code, "error": message[:300]})
            mark_failed(self.ctx, job, code, _user_message(code, message))

    def reclaim(self, conn: db.Conn) -> None:
        now = time.monotonic()
        if now - self._last_reclaim < RECLAIM_EVERY_S:
            return
        self._last_reclaim = now
        for job in jobs.reclaim_stale(conn, self.settings.job_lock_timeout_s):
            logger.error("job lock expired, out of attempts", extra={"job_id": str(job["id"])})
            mark_failed(self.ctx, job, "timeout", "Processing took too long. Try again.")

    def maintain(self) -> None:
        now = time.monotonic()
        if self._last_maintenance and now - self._last_maintenance < MAINTENANCE_EVERY_S:
            return
        self._last_maintenance = now
        try:
            with db.connect(self.settings.database_url, autocommit=True) as conn:
                maintenance.run(
                    conn,
                    self.ctx.storage,
                    abandoned_hours=self.settings.abandoned_upload_hours,
                    retention_days=self.settings.raw_retention_days_after_approval,
                )
        except Exception:
            logger.exception("maintenance failed")

    def serve(self) -> None:
        url = self.settings.database_url
        logger.info("worker starting", extra={"worker_id": self.worker_id})
        while not self.stopping:
            try:
                with db.connect(url) as conn, db.connect(url, autocommit=True) as listen:
                    listen.execute(f"listen {jobs.NOTIFY_CHANNEL}")
                    while not self.stopping:
                        self.reclaim(conn)
                        self.maintain()
                        if self.run_one(conn):
                            continue
                        # Idle: wait for a notification or the poll timeout.
                        for _ in listen.notifies(timeout=self.settings.job_poll_s, stop_after=1):
                            pass
            except psycopg.OperationalError:
                logger.exception("database connection lost; reconnecting in 5 s")
                time.sleep(5)
        logger.info("worker stopped", extra={"worker_id": self.worker_id})


_MESSAGES = {
    "unreadable": "This file couldn't be read as a video.",
    "no_video_stream": "This file has no video track.",
    "timeout": "Processing took too long. Try again.",
    "download_failed": "Couldn't fetch the upload. Try again.",
    "upload_failed": "Couldn't save the processed video. Try again.",
    "internal_error": "Something went wrong while processing. Try again.",
}


def _user_message(code: str, message: str) -> str:
    # MediaError messages are written for users; infrastructure errors get a generic one.
    return _MESSAGES.get(code, message)


def main() -> None:
    tlog.setup(os.environ.get("LOG_LEVEL", "INFO"))
    settings = get_settings()
    worker = Worker(settings, from_settings())

    def _stop(*_: object) -> None:
        logger.info("shutdown requested; finishing the current job")
        worker.stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    worker.serve()


if __name__ == "__main__":
    main()
