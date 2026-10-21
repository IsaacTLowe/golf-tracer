"""Job handlers: process (probe → normalize → auto-trace → thumbnail), retrace and render."""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tracer_core import media, storage, tracers
from tracer_core.config import Settings
from tracer_core.db import Conn, Row, jsonb
from tracer_core.pipeline import TraceResult, auto_trace
from tracer_core.render import render_video
from tracer_core.storage import Storage

logger = logging.getLogger("tracer_worker")


class ClipGone(Exception):
    """The clip was deleted while the job ran; drop the work quietly."""


class JobError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class Context:
    settings: Settings
    storage: Storage
    connect: Any  # () -> Conn, a fresh autocommit=False connection


@contextmanager
def workdir(settings: Settings) -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="tracer-job-", dir=settings.work_dir))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _clip(conn: Conn, clip_id: str, *, lock: bool = False) -> Row:
    row = conn.execute(
        "select * from public.clips where id = %s" + (" for update" if lock else ""), (clip_id,)
    ).fetchone()
    if row is None:
        raise ClipGone(clip_id)
    return row


def _set_status(conn: Conn, clip_id: str, status: str, **fields: Any) -> None:
    sets = ["status = %s"]
    params: list[Any] = [status]
    for k, v in fields.items():
        sets.append(f"{k} = %s")
        params.append(jsonb(v) if isinstance(v, dict) else v)
    cur = conn.execute(f"update public.clips set {', '.join(sets)} where id = %s", [*params, clip_id])
    if cur.rowcount == 0:
        raise ClipGone(clip_id)


def _default_style(conn: Conn, user_id: str) -> dict[str, Any] | None:
    row = conn.execute("select default_tracer_style from public.profiles where id = %s", (user_id,)).fetchone()
    return row["default_tracer_style"] if row else None


def _video_meta(clip: Row) -> dict[str, Any]:
    return {"width": clip["width"], "height": clip["height"], "fps": clip["fps"], "duration_s": clip["duration_s"]}


def _trace(ctx: Context, video: Path, meta: dict[str, Any], hints: dict[str, Any], style: dict[str, Any] | None,
           user_id: str, clip_id: str, work: Path) -> TraceResult:  # fmt: skip
    debug_out = work / "debug.mp4" if ctx.settings.trace_debug else None
    try:
        audio = media.extract_audio(video)
        result = auto_trace(video, meta, audio=audio, hints=hints, style=style, debug_out=debug_out)
    except Exception:
        # Tracing must never strand a clip: fall back to the manual wizard.
        logger.exception("auto trace crashed", extra={"clip_id": clip_id})
        return TraceResult(tracer=None, confidence=0.0, route="needs_input", reason="auto tracing failed")
    if debug_out is not None and debug_out.exists():
        try:
            ctx.storage.upload_file(storage.MEDIA, storage.debug_path(user_id, clip_id), debug_out, "video/mp4")
        except Exception:
            logger.exception("debug upload failed", extra={"clip_id": clip_id})
    logger.info(
        "traced",
        extra={"clip_id": clip_id, "route": result.route, "confidence": result.confidence,
               "impact_source": result.impact_source, "timings": result.timings},
    )  # fmt: skip
    return result


def _save_trace(conn: Conn, clip_id: str, result: TraceResult, extra: dict[str, Any] | None = None) -> None:
    detection = result.detection()
    fields: dict[str, Any] = {"detection": detection, "needs_check": result.route == "review_check", **(extra or {})}
    if result.tracer is not None and result.route != "needs_input":
        tracers.insert_version(conn, clip_id, "auto", result.tracer, result.confidence)
        _set_status(conn, clip_id, "review", approved_at=None, **fields)
    else:
        fields["needs_check"] = False
        _set_status(conn, clip_id, "needs_input", approved_at=None, **fields)


def handle_process(ctx: Context, job: Row) -> None:
    clip_id = str(job["clip_id"])
    with ctx.connect() as conn:
        clip = _clip(conn, clip_id)
        _set_status(conn, clip_id, "processing", error_code=None, error_message=None)
        conn.commit()
        style = _default_style(conn, str(clip["user_id"]))
    user_id = str(clip["user_id"])
    s = ctx.settings
    with workdir(s) as work:
        raw = work / f"original{Path(clip['raw_path']).suffix}"
        try:
            ctx.storage.download_to(storage.RAW, clip["raw_path"], raw)
        except storage.StorageError as e:
            raise JobError("download_failed", str(e), retryable=True) from e
        try:
            probe = media.probe(raw)
            media.check_limits(probe, min_duration=s.min_duration_s, max_duration=s.max_duration_s,
                               min_short_edge=s.min_short_edge_px)  # fmt: skip
            if raw.stat().st_size > s.max_clip_bytes:
                raise media.MediaError("file_too_large", "Over 500 MB.")
            normalized = work / "normalized.mp4"
            media.normalize(raw, normalized, probe)
            norm = media.probe(normalized)
        except media.MediaError as e:
            raise JobError(e.code, str(e), retryable=e.retryable) from e

        meta = {"width": norm.width, "height": norm.height, "fps": norm.nominal_fps, "duration_s": norm.duration_s}
        result = _trace(ctx, normalized, meta, dict(job["payload"] or {}), style, user_id, clip_id, work)

        thumb = work / "thumb.jpg"
        thumb_t = (result.impact_time_s - 0.15) if result.impact_time_s is not None else norm.duration_s * 0.3
        try:
            media.thumbnail(normalized, thumb, thumb_t)
        except media.MediaError as e:
            raise JobError(e.code, str(e), retryable=True) from e

        npath, tpath = storage.normalized_path(user_id, clip_id), storage.thumbnail_path(user_id, clip_id)
        try:
            ctx.storage.upload_file(storage.MEDIA, npath, normalized, "video/mp4")
            ctx.storage.upload_file(storage.MEDIA, tpath, thumb, "image/jpeg")
        except storage.StorageError as e:
            raise JobError("upload_failed", str(e), retryable=True) from e

    try:
        with ctx.connect() as conn:
            _clip(conn, clip_id, lock=True)
            extra = {
                "normalized_path": npath, "thumbnail_path": tpath, "width": norm.width, "height": norm.height,
                "fps": norm.nominal_fps, "duration_s": norm.duration_s, "error_code": None, "error_message": None,
            }  # fmt: skip
            _save_trace(conn, clip_id, result, extra)
            conn.commit()
    except ClipGone:
        # Deleted while we worked: remove what we just uploaded.
        ctx.storage.delete(storage.MEDIA, [npath, tpath, storage.debug_path(user_id, clip_id)])
        raise


def handle_retrace(ctx: Context, job: Row) -> None:
    clip_id = str(job["clip_id"])
    with ctx.connect() as conn:
        clip = _clip(conn, clip_id)
        _set_status(conn, clip_id, "processing")
        conn.commit()
        cur = tracers.current(conn, clip_id)
        style = dict(cur["data"]["style"]) if cur else _default_style(conn, str(clip["user_id"]))
    user_id = str(clip["user_id"])
    with workdir(ctx.settings) as work:
        video = work / "normalized.mp4"
        try:
            ctx.storage.download_to(storage.MEDIA, clip["normalized_path"], video)
        except storage.StorageError as e:
            raise JobError("download_failed", str(e), retryable=True) from e
        result = _trace(ctx, video, _video_meta(clip), dict(job["payload"] or {}), style, user_id, clip_id, work)
    with ctx.connect() as conn:
        _clip(conn, clip_id, lock=True)
        _save_trace(conn, clip_id, result)
        conn.commit()


def handle_render(ctx: Context, job: Row) -> None:
    clip_id = str(job["clip_id"])
    version = int((job["payload"] or {}).get("tracer_version") or 0)
    with ctx.connect() as conn:
        clip = _clip(conn, clip_id)
        row = conn.execute(
            "select data from public.tracers where clip_id = %s and version = %s", (clip_id, version)
        ).fetchone()
    if row is None:
        raise JobError("tracer_missing", f"Tracer version {version} not found.", retryable=False)
    user_id = str(clip["user_id"])
    out_path = storage.render_path(user_id, clip_id, version)
    with workdir(ctx.settings) as work:
        src, dst = work / "normalized.mp4", work / "render.mp4"
        try:
            ctx.storage.download_to(storage.MEDIA, clip["normalized_path"], src)
        except storage.StorageError as e:
            raise JobError("download_failed", str(e), retryable=True) from e
        watermark = f"Traced with {ctx.settings.app_name}" if ctx.settings.watermark else None
        try:
            render_video(src, dst, row["data"], watermark=watermark)
        except media.MediaError as e:
            raise JobError(e.code, str(e), retryable=e.retryable) from e
        try:
            ctx.storage.upload_file(storage.RENDERS, out_path, dst, "video/mp4")
        except storage.StorageError as e:
            raise JobError("upload_failed", str(e), retryable=True) from e
    with ctx.connect() as conn:
        _clip(conn, clip_id, lock=True)
        conn.execute(
            """
            update public.clips
               set render_path = %s,
                   status = case when status = 'rendering' then 'approved'::public.clip_status else status end
             where id = %s
            """,
            (out_path, clip_id),
        )
        conn.commit()


HANDLERS = {"process": handle_process, "retrace": handle_retrace, "render": handle_render}


def mark_failed(ctx: Context, job: Row, code: str, message: str) -> None:
    """Final failure: put the clip somewhere the user can act on."""
    clip_id = str(job["clip_id"])
    with ctx.connect() as conn:
        clip = conn.execute("select status, normalized_path from public.clips where id = %s", (clip_id,)).fetchone()
        if clip is None:
            return
        if job["kind"] == "render":
            # The tracer is still approved; the user can retry the export.
            conn.execute(
                "update public.clips set status = 'approved', error_code = %s, error_message = %s where id = %s",
                (code, message[:500], clip_id),
            )
        elif job["kind"] == "retrace" and clip["normalized_path"]:
            conn.execute(
                "update public.clips set status = 'needs_input', error_code = %s, error_message = %s where id = %s",
                (code, message[:500], clip_id),
            )
        else:
            conn.execute(
                "update public.clips set status = 'failed', error_code = %s, error_message = %s where id = %s",
                (code, message[:500], clip_id),
            )
        conn.commit()
