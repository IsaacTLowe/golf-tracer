"""Clip and tracer business logic. Every query is scoped to the authenticated user, even
though the API connects with service-role privileges."""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from pathlib import PurePath
from typing import Any

from tracer_core import jobs, storage, tracers
from tracer_core.config import Settings
from tracer_core.db import Conn, Row, jsonb
from tracer_core.model import APEX_TIME_FRACTION, ModelError, build_tracer, resolve_style
from tracer_core.storage import Storage, StorageError
from tracer_spec import validate

from .errors import ApiError, not_found
from .schemas import ClipDetail, ClipOut, TracerOut

TUS_CHUNK_SIZE = 6 * 1024 * 1024  # Supabase requires exactly 6 MB chunks
CONTENT_TYPES = {
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
    "video/x-m4v": "mp4",
}
EXTENSIONS = {"mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "webm": "video/webm"}
TRACEABLE = ("review", "needs_input", "approved")
_RENDER_RE = re.compile(r"render_v(\d+)\.mp4$")


# ----------------------------------------------------------------------------------------------
# Row → model
# ----------------------------------------------------------------------------------------------
def render_version(path: str | None) -> int | None:
    m = _RENDER_RE.search(path or "")
    return int(m.group(1)) if m else None


def clip_out(row: Row, *, thumbnail_url: str | None = None, model: type[ClipOut] = ClipOut, **extra: Any) -> Any:
    return model(
        id=str(row["id"]),
        batch_id=str(row["batch_id"]) if row["batch_id"] else None,
        original_filename=row["original_filename"],
        status=row["status"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        duration_s=row["duration_s"],
        fps=row["fps"],
        width=row["width"],
        height=row["height"],
        size_bytes=row["size_bytes"],
        needs_check=row["needs_check"],
        approved_at=row["approved_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        thumbnail_url=thumbnail_url,
        current_tracer_version=row.get("current_tracer_version"),
        render_version=render_version(row["render_path"]),
        **extra,
    )


def tracer_out(row: Row) -> TracerOut:
    return TracerOut.model_validate(
        {
            "id": str(row["id"]),
            "clip_id": str(row["clip_id"]),
            "version": row["version"],
            "source": row["source"],
            "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
            "created_at": row["created_at"],
            "data": row["data"],
        }
    )


_CLIP_SELECT = """
    select c.*, t.version as current_tracer_version
      from public.clips c
      left join public.tracers t on t.clip_id = c.id and t.is_current
"""


def get_clip(conn: Conn, user_id: str, clip_id: str, *, lock: bool = False) -> Row:
    if not _is_uuid(clip_id):
        raise not_found()
    if lock:
        row = conn.execute(
            "select * from public.clips where id = %s and user_id = %s for update", (clip_id, user_id)
        ).fetchone()
        if row is not None:
            cur = tracers.current(conn, clip_id)
            row["current_tracer_version"] = cur["version"] if cur else None
    else:
        row = conn.execute(_CLIP_SELECT + " where c.id = %s and c.user_id = %s", (clip_id, user_id)).fetchone()
    if row is None:
        raise not_found()
    return row


def _is_uuid(v: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", v))


# ----------------------------------------------------------------------------------------------
# Batches and uploads
# ----------------------------------------------------------------------------------------------
def create_batch(conn: Conn, user_id: str, clip_count: int) -> Row:
    row = conn.execute(
        "insert into public.upload_batches (user_id, clip_count) values (%s, %s) returning *", (user_id, clip_count)
    ).fetchone()
    assert row is not None
    return row


def check_rate_limit(conn: Conn, user_id: str, limit: int) -> None:
    row = conn.execute(
        """
        select count(*) as n, min(created_at) as oldest
          from public.clips
         where user_id = %s and created_at > now() - interval '1 hour'
        """,
        (user_id,),
    ).fetchone()
    assert row is not None
    if row["n"] >= limit:
        wait = conn.execute(
            "select greatest(1, ceil(extract(epoch from (%s::timestamptz + interval '1 hour' - now()))))::int as s",
            (row["oldest"],),
        ).fetchone()
        retry = str(wait["s"] if wait else 60)
        raise ApiError(
            429, "rate_limited", f"Upload limit reached ({limit} clips per hour). Try again later.",
            headers={"Retry-After": retry},
        )  # fmt: skip


def resolve_extension(filename: str, content_type: str) -> str:
    ext = PurePath(filename).suffix.lower().lstrip(".")
    ct = content_type.split(";")[0].strip().lower()
    if ct in CONTENT_TYPES:
        return CONTENT_TYPES[ct] if ext not in EXTENSIONS else ("mp4" if ext == "m4v" else ext)
    if ext in EXTENSIONS and ct in ("", "application/octet-stream"):
        return "mp4" if ext == "m4v" else ext
    raise ApiError(415, "unsupported_type", "Only MP4, MOV or WebM videos are accepted.")


def validate_upload(
    settings: Settings, size_bytes: int, duration_s: float | None, width: int | None, height: int | None
) -> None:
    if size_bytes > settings.max_clip_bytes:
        raise ApiError(413, "file_too_large", "Over 500 MB.")
    if duration_s is not None and duration_s > 0:
        if duration_s < settings.min_duration_s - 0.05:
            raise ApiError(422, "too_short", f"Shorter than {settings.min_duration_s:g} s.")
        if duration_s > settings.max_duration_s + 0.5:
            raise ApiError(422, "too_long", f"Longer than {settings.max_duration_s:g} s.")
    if width and height and min(width, height) < settings.min_short_edge_px:
        raise ApiError(422, "resolution_too_low", "Below 480p.")


def create_clip(
    conn: Conn,
    settings: Settings,
    user_id: str,
    *,
    filename: str,
    size_bytes: int,
    content_type: str,
    duration_s: float | None,
    width: int | None,
    height: int | None,
    batch_id: str | None,
) -> tuple[Row, str, str]:
    ext = resolve_extension(filename, content_type)
    validate_upload(settings, size_bytes, duration_s, width, height)
    if batch_id is not None:
        if not _is_uuid(batch_id):
            raise not_found("batch")
        b = conn.execute(
            "select id from public.upload_batches where id = %s and user_id = %s", (batch_id, user_id)
        ).fetchone()
        if b is None:
            raise not_found("batch")
    check_rate_limit(conn, user_id, settings.clip_rate_limit_per_hour)
    row = conn.execute(
        """
        insert into public.clips (user_id, batch_id, original_filename, status, size_bytes, duration_s, width, height)
        values (%s, %s, %s, 'uploading', %s, %s, %s, %s)
        returning *
        """,
        (user_id, batch_id, filename, size_bytes, duration_s, width, height),
    ).fetchone()
    assert row is not None
    path = storage.raw_path(user_id, str(row["id"]), ext)
    row = conn.execute("update public.clips set raw_path = %s where id = %s returning *", (path, row["id"])).fetchone()
    assert row is not None
    row["current_tracer_version"] = None
    return row, path, EXTENSIONS[ext]


def complete_upload(conn: Conn, store: Storage, settings: Settings, user_id: str, clip_id: str) -> Row:
    clip = get_clip(conn, user_id, clip_id, lock=True)
    if clip["status"] != "uploading":
        return clip  # idempotent: a retried `complete` is harmless
    try:
        size = store.object_size(storage.RAW, clip["raw_path"])
    except StorageError as e:
        raise ApiError(502, "storage_unavailable", "Could not reach storage; try again.") from e
    if size is None:
        raise ApiError(409, "upload_missing", "The uploaded file wasn't found. Upload it again.")
    if size > settings.max_clip_bytes:
        raise ApiError(413, "file_too_large", "Over 500 MB.")
    row = conn.execute(
        """
        update public.clips set status = 'queued', size_bytes = coalesce(nullif(%s, 0), size_bytes),
               error_code = null, error_message = null
         where id = %s returning *
        """,
        (size, clip_id),
    ).fetchone()
    assert row is not None
    jobs.enqueue(conn, clip_id, "process")
    row["current_tracer_version"] = None
    return row


# ----------------------------------------------------------------------------------------------
# Listing
# ----------------------------------------------------------------------------------------------
def encode_cursor(created_at: datetime, clip_id: str) -> str:
    raw = json.dumps([created_at.isoformat(), clip_id]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        pad = "=" * (-len(cursor) % 4)
        created_at, clip_id = json.loads(base64.urlsafe_b64decode(cursor + pad))
        datetime.fromisoformat(created_at)
        if not _is_uuid(clip_id):
            raise ValueError
        return created_at, clip_id
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        raise ApiError(400, "invalid_cursor", "Invalid pagination cursor.") from e


def list_clips(
    conn: Conn, user_id: str, *, statuses: list[str] | None, batch_id: str | None, cursor: str | None, limit: int
) -> tuple[list[Row], str | None]:
    where = ["c.user_id = %s"]
    params: list[Any] = [user_id]
    if statuses:
        where.append("c.status = any(%s::public.clip_status[])")
        params.append(statuses)
    if batch_id:
        if not _is_uuid(batch_id):
            return [], None
        where.append("c.batch_id = %s")
        params.append(batch_id)
    if cursor:
        ts, cid = decode_cursor(cursor)
        where.append("(c.created_at, c.id) < (%s::timestamptz, %s::uuid)")
        params += [ts, cid]
    params.append(limit + 1)
    rows = conn.execute(
        _CLIP_SELECT + " where " + " and ".join(where) + " order by c.created_at desc, c.id desc limit %s", params
    ).fetchall()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor(last["created_at"], str(last["id"]))
    return rows, next_cursor


def thumbnail_urls(store: Storage, rows: list[Row], ttl: int) -> dict[str, str]:
    paths = [r["thumbnail_path"] for r in rows if r["thumbnail_path"]]
    try:
        return store.sign_many(storage.MEDIA, paths, ttl)
    except StorageError:
        return {}


def clip_detail(conn: Conn, store: Storage, settings: Settings, row: Row) -> ClipDetail:
    ttl = settings.signed_url_ttl_s
    thumb = playback = debug = None
    try:
        if row["thumbnail_path"]:
            thumb = store.sign(storage.MEDIA, row["thumbnail_path"], ttl)
        if row["normalized_path"]:
            playback = store.sign(storage.MEDIA, row["normalized_path"], ttl)
        if settings.trace_debug and row["normalized_path"]:
            dbg = storage.debug_path(str(row["user_id"]), str(row["id"]))
            if store.object_size(storage.MEDIA, dbg) is not None:
                debug = store.sign(storage.MEDIA, dbg, ttl)
    except StorageError:
        pass
    cur = tracers.current(conn, str(row["id"]))
    detail: ClipDetail = clip_out(
        row,
        thumbnail_url=thumb,
        model=ClipDetail,
        playback_url=playback,
        debug_url=debug,
        tracer=tracer_out(cur) if cur else None,
        detection=row.get("detection"),
    )
    return detail


# ----------------------------------------------------------------------------------------------
# Delete
# ----------------------------------------------------------------------------------------------
def delete_clip(conn: Conn, store: Storage, user_id: str, clip_id: str) -> None:
    clip = get_clip(conn, user_id, clip_id, lock=True)
    prefix = f"{user_id}/{clip_id}"
    try:
        for bucket in (storage.RAW, storage.MEDIA, storage.RENDERS):
            paths = store.list_prefix(bucket, prefix)
            if bucket == storage.RAW and clip["raw_path"] and clip["raw_path"] not in paths:
                paths.append(clip["raw_path"])
            if paths:
                store.delete(bucket, paths)
    except StorageError as e:
        raise ApiError(502, "storage_unavailable", "Could not delete the stored files; try again.") from e
    conn.execute("delete from public.clips where id = %s and user_id = %s", (clip_id, user_id))


# ----------------------------------------------------------------------------------------------
# Tracing
# ----------------------------------------------------------------------------------------------
def retrace(conn: Conn, user_id: str, clip_id: str, hints: dict[str, Any]) -> Row:
    clip = get_clip(conn, user_id, clip_id, lock=True)
    if clip["status"] in ("uploading", "queued", "processing", "rendering"):
        raise ApiError(409, "clip_busy", f"Clip is {clip['status']}; wait for it to finish.")
    if clip["normalized_path"] is None:
        kind: jobs.JobKind = "process"  # failed before normalization: start over
    else:
        kind = "retrace"
    row = conn.execute(
        """
        update public.clips set status = 'queued', error_code = null, error_message = null, approved_at = null
         where id = %s returning *
        """,
        (clip_id,),
    ).fetchone()
    assert row is not None
    jobs.enqueue(conn, clip_id, kind, {k: v for k, v in hints.items() if v is not None})
    row["current_tracer_version"] = clip["current_tracer_version"]
    return row


def _video_meta(clip: Row) -> dict[str, Any]:
    if not clip["normalized_path"] or not clip["width"] or not clip["fps"] or not clip["duration_s"]:
        raise ApiError(409, "not_ready", "The clip hasn't finished processing yet.")
    return {"width": clip["width"], "height": clip["height"], "fps": clip["fps"], "duration_s": clip["duration_s"]}


def default_style(conn: Conn, user_id: str, clip_id: str) -> dict[str, Any]:
    cur = tracers.current(conn, clip_id)
    if cur is not None:
        return dict(cur["data"]["style"])
    prof = conn.execute("select default_tracer_style from public.profiles where id = %s", (user_id,)).fetchone()
    return resolve_style(prof["default_tracer_style"] if prof else None)


def _mark_edited(conn: Conn, clip: Row) -> None:
    # A new tracer version needs review again (approved → review, needs_input → review).
    conn.execute(
        "update public.clips set status = 'review', approved_at = null, needs_check = false where id = %s",
        (clip["id"],),
    )


def manual_tracer(conn: Conn, user_id: str, clip_id: str, req: dict[str, Any]) -> Row:
    clip = get_clip(conn, user_id, clip_id, lock=True)
    if clip["status"] not in TRACEABLE:
        raise ApiError(409, "clip_busy", f"Clip is {clip['status']}; it can't be traced right now.")
    video = _video_meta(clip)
    style = req.get("style") or default_style(conn, user_id, clip_id)
    impact = float(req["impact_time_s"])
    if impact >= video["duration_s"]:
        raise ApiError(422, "impact_out_of_range", "Impact time is past the end of the clip.")
    landing_t = req.get("landing_time_s")
    apex_t = None
    if landing_t is not None:
        landing_t = min(float(landing_t), video["duration_s"])
        if landing_t <= impact + 2.0 / video["fps"]:
            raise ApiError(422, "landing_before_impact", "Landing time must be after impact.")
        apex_t = impact + APEX_TIME_FRACTION * (landing_t - impact)
    try:
        doc = build_tracer(
            video=video,
            impact_time_s=impact,
            start=list(req["start"]),
            apex=list(req["apex"]),
            landing=list(req["landing"]),
            curve=float(req.get("curve") or 0.0),
            style=style,
            apex_time_s=apex_t,
            landing_time_s=landing_t,
        )
    except ModelError as e:
        raise ApiError(422, e.code, str(e)) from e
    row = tracers.insert_version(conn, clip_id, "manual", doc, None)
    _mark_edited(conn, clip)
    return row


def save_edited_tracer(conn: Conn, user_id: str, clip_id: str, base_version: int, data: dict[str, Any]) -> Row:
    clip = get_clip(conn, user_id, clip_id, lock=True)
    if clip["status"] not in TRACEABLE:
        raise ApiError(409, "clip_busy", f"Clip is {clip['status']}; it can't be edited right now.")
    cur = tracers.current(conn, clip_id)
    if cur is None or cur["version"] != base_version:
        raise ApiError(
            409, "version_conflict", "This tracer changed since you opened it. Reload to get the latest version.",
            details={"current_version": cur["version"] if cur else None},
        )  # fmt: skip
    doc = {k: v for k, v in data.items() if v is not None and k not in ("observed", "fit")}
    doc["video"] = _video_meta(clip)  # the server's view of the video is authoritative
    errors = validate(doc)
    if errors:
        raise ApiError(422, "invalid_tracer", "; ".join(errors), details=errors)
    row = tracers.insert_version(conn, clip_id, "edited", doc, None)
    _mark_edited(conn, clip)
    return row


def approve(conn: Conn, user_id: str, clip_id: str) -> Row:
    clip = get_clip(conn, user_id, clip_id, lock=True)
    if clip["status"] == "approved":
        return clip
    if clip["status"] != "review" or clip["current_tracer_version"] is None:
        raise ApiError(409, "not_reviewable", "Only a clip with a tracer waiting for review can be approved.")
    row = conn.execute(
        "update public.clips set status = 'approved', approved_at = now() where id = %s returning *", (clip_id,)
    ).fetchone()
    assert row is not None
    row["current_tracer_version"] = clip["current_tracer_version"]
    return row


def request_render(conn: Conn, store: Storage, user_id: str, clip_id: str) -> tuple[Row, bool]:
    clip = get_clip(conn, user_id, clip_id, lock=True)
    version = clip["current_tracer_version"]
    if clip["status"] == "rendering":
        return clip, False
    if clip["status"] != "approved" or version is None:
        raise ApiError(409, "not_approved", "Approve the tracer before exporting.")
    if render_version(clip["render_path"]) == version:
        try:
            exists = store.object_size(storage.RENDERS, clip["render_path"]) is not None
        except StorageError:
            exists = False
        if exists:
            return clip, True
    row = conn.execute("update public.clips set status = 'rendering' where id = %s returning *", (clip_id,)).fetchone()
    assert row is not None
    jobs.enqueue(conn, clip_id, "render", {"tracer_version": version})
    row["current_tracer_version"] = version
    return row, False


def render_link(store: Storage, settings: Settings, clip: Row) -> dict[str, Any]:
    v = render_version(clip["render_path"])
    if v is None:
        raise not_found("render")
    stem = PurePath(clip["original_filename"]).stem or "clip"
    try:
        url = store.sign(storage.RENDERS, clip["render_path"], settings.signed_url_ttl_s, download=f"{stem}-traced.mp4")
    except StorageError as e:
        raise ApiError(502, "storage_unavailable", "Could not reach storage; try again.") from e
    return {"url": url, "tracer_version": v, "current": v == clip["current_tracer_version"]}


def update_profile(conn: Conn, user_id: str, fields: dict[str, Any]) -> Row:
    conn.execute("insert into public.profiles (id) values (%s) on conflict (id) do nothing", (user_id,))
    sets, params = [], []
    if "display_name" in fields:
        sets.append("display_name = %s")
        params.append(fields["display_name"])
    if "default_tracer_style" in fields:
        sets.append("default_tracer_style = %s")
        style = fields["default_tracer_style"]
        params.append(jsonb(style) if style is not None else None)
    if sets:
        conn.execute(f"update public.profiles set {', '.join(sets)} where id = %s", [*params, user_id])
    row = conn.execute("select * from public.profiles where id = %s", (user_id,)).fetchone()
    assert row is not None
    return row
