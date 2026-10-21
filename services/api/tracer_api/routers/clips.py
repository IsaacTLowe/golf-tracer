from __future__ import annotations

from typing import Annotated, get_args

from fastapi import APIRouter, Depends, Query, Response
from tracer_core import db
from tracer_core.config import Settings
from tracer_core.storage import Storage

from .. import service
from ..auth import AuthUser, current_user
from ..deps import get_settings_dep, get_storage
from ..errors import ERRORS, ApiError
from ..schemas import (
    BatchCreate,
    BatchOut,
    BulkRequest,
    BulkResponse,
    BulkResult,
    ClipCreate,
    ClipCreated,
    ClipDetail,
    ClipOut,
    ClipPage,
    ClipStatus,
    ManualTracerRequest,
    RenderEnqueued,
    RenderLink,
    RetraceRequest,
    TracerOut,
    TracerUpdate,
    TracerVersions,
    UploadTarget,
)

router = APIRouter(prefix="/v1", responses=ERRORS)

User = Annotated[AuthUser, Depends(current_user)]
Store = Annotated[Storage, Depends(get_storage)]
Conf = Annotated[Settings, Depends(get_settings_dep)]


@router.post("/batches", response_model=BatchOut, status_code=201, tags=["uploads"], operation_id="createBatch")
def create_batch(body: BatchCreate, user: User, settings: Conf) -> BatchOut:
    if body.clip_count > settings.max_batch_size:
        raise ApiError(422, "batch_too_large", f"At most {settings.max_batch_size} clips per batch.")
    with db.transaction() as conn:
        row = service.create_batch(conn, user.id, body.clip_count)
    return BatchOut(id=str(row["id"]), clip_count=row["clip_count"], created_at=row["created_at"])


@router.post(
    "/clips",
    response_model=ClipCreated,
    status_code=201,
    tags=["uploads"],
    operation_id="createClip",
    responses={413: ERRORS[422], 415: ERRORS[422], 429: ERRORS[422]},
)
def create_clip(body: ClipCreate, user: User, settings: Conf) -> ClipCreated:
    with db.transaction() as conn:
        row, path, content_type = service.create_clip(
            conn,
            settings,
            user.id,
            filename=body.filename,
            size_bytes=body.size_bytes,
            content_type=body.content_type,
            duration_s=body.duration_s,
            width=body.width,
            height=body.height,
            batch_id=body.batch_id,
        )
    return ClipCreated(
        clip=service.clip_out(row),
        upload=UploadTarget(
            bucket="raw",
            object_path=path,
            tus_endpoint=settings.public_supabase_url.rstrip("/") + "/storage/v1/upload/resumable",
            chunk_size=service.TUS_CHUNK_SIZE,
            content_type=content_type,
        ),
    )


@router.post("/clips/{clip_id}/complete", response_model=ClipOut, tags=["uploads"], operation_id="completeUpload")
def complete_upload(clip_id: str, user: User, store: Store, settings: Conf) -> ClipOut:
    with db.transaction() as conn:
        row = service.complete_upload(conn, store, settings, user.id, clip_id)
    out: ClipOut = service.clip_out(row)
    return out


@router.get("/clips", response_model=ClipPage, tags=["clips"], operation_id="listClips")
def list_clips(
    user: User,
    store: Store,
    settings: Conf,
    status: Annotated[
        list[str] | None,
        Query(description="Clip statuses to include; repeat the parameter or comma-separate values"),
    ] = None,
    batch_id: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ClipPage:
    statuses: list[str] | None = None
    if status:
        statuses = [s.strip() for item in status for s in item.split(",") if s.strip()]
        unknown = sorted(set(statuses) - set(get_args(ClipStatus)))
        if unknown:
            raise ApiError(422, "validation_error", f"Unknown status: {', '.join(unknown)}")
    with db.transaction() as conn:
        rows, next_cursor = service.list_clips(
            conn, user.id, statuses=statuses, batch_id=batch_id, cursor=cursor, limit=limit
        )
    thumbs = service.thumbnail_urls(store, rows, settings.signed_url_ttl_s)
    return ClipPage(
        items=[service.clip_out(r, thumbnail_url=thumbs.get(r["thumbnail_path"] or "")) for r in rows],
        next_cursor=next_cursor,
    )


@router.get("/clips/{clip_id}", response_model=ClipDetail, tags=["clips"], operation_id="getClip")
def get_clip(clip_id: str, user: User, store: Store, settings: Conf) -> ClipDetail:
    with db.transaction() as conn:
        row = service.get_clip(conn, user.id, clip_id)
        return service.clip_detail(conn, store, settings, row)


@router.delete("/clips/{clip_id}", status_code=204, tags=["clips"], operation_id="deleteClip")
def delete_clip(clip_id: str, user: User, store: Store) -> Response:
    with db.transaction() as conn:
        service.delete_clip(conn, store, user.id, clip_id)
    return Response(status_code=204)


@router.post("/clips/{clip_id}/retrace", response_model=ClipOut, tags=["tracing"], operation_id="retraceClip")
def retrace(clip_id: str, user: User, body: RetraceRequest | None = None) -> ClipOut:
    hints = body.model_dump() if body else {}
    if hints.get("ball_xy") is not None:
        hints["ball_xy"] = list(hints["ball_xy"])
    with db.transaction() as conn:
        row = service.retrace(conn, user.id, clip_id, hints)
    out: ClipOut = service.clip_out(row)
    return out


@router.post(
    "/clips/{clip_id}/tracer/manual",
    response_model=TracerOut,
    status_code=201,
    tags=["tracing"],
    operation_id="createManualTracer",
)
def manual_tracer(clip_id: str, body: ManualTracerRequest, user: User) -> TracerOut:
    req = body.model_dump()
    if body.style is not None:
        req["style"] = body.style.model_dump()
    with db.transaction() as conn:
        row = service.manual_tracer(conn, user.id, clip_id, req)
    return service.tracer_out(row)


@router.put("/clips/{clip_id}/tracer", response_model=TracerOut, tags=["tracing"], operation_id="saveTracer")
def save_tracer(clip_id: str, body: TracerUpdate, user: User) -> TracerOut:
    data = body.data.model_dump(mode="json")
    data["path"] = {k: list(v) for k, v in data["path"].items()}
    with db.transaction() as conn:
        row = service.save_edited_tracer(conn, user.id, clip_id, body.base_version, data)
    return service.tracer_out(row)


@router.get(
    "/clips/{clip_id}/tracer/versions",
    response_model=TracerVersions,
    tags=["tracing"],
    operation_id="listTracerVersions",
)
def tracer_versions(clip_id: str, user: User) -> TracerVersions:
    from tracer_core import tracers

    with db.transaction() as conn:
        service.get_clip(conn, user.id, clip_id)
        rows = tracers.versions(conn, clip_id)
    return TracerVersions(items=[service.tracer_out(r) for r in rows])


@router.post("/clips/{clip_id}/approve", response_model=ClipOut, tags=["review"], operation_id="approveClip")
def approve(clip_id: str, user: User) -> ClipOut:
    with db.transaction() as conn:
        row = service.approve(conn, user.id, clip_id)
    out: ClipOut = service.clip_out(row)
    return out


@router.post("/clips/{clip_id}/render", response_model=RenderEnqueued, tags=["export"], operation_id="renderClip")
def render(clip_id: str, user: User, store: Store) -> RenderEnqueued:
    with db.transaction() as conn:
        row, reused = service.request_render(conn, store, user.id, clip_id)
    return RenderEnqueued(clip=service.clip_out(row), reused=reused)


@router.get("/clips/{clip_id}/render", response_model=RenderLink, tags=["export"], operation_id="getRender")
def get_render(clip_id: str, user: User, store: Store, settings: Conf) -> RenderLink:
    with db.transaction() as conn:
        row = service.get_clip(conn, user.id, clip_id)
    return RenderLink(**service.render_link(store, settings, row))


@router.post("/clips/bulk", response_model=BulkResponse, tags=["review"], operation_id="bulkClips")
def bulk(body: BulkRequest, user: User, store: Store) -> BulkResponse:
    results: list[BulkResult] = []
    for clip_id in dict.fromkeys(body.clip_ids):
        try:
            with db.transaction() as conn:
                if body.action == "approve":
                    service.approve(conn, user.id, clip_id)
                elif body.action == "render":
                    service.request_render(conn, store, user.id, clip_id)
                else:
                    service.delete_clip(conn, store, user.id, clip_id)
            results.append(BulkResult(clip_id=clip_id, ok=True))
        except ApiError as e:
            results.append(BulkResult(clip_id=clip_id, ok=False, error=e.code))
    return BulkResponse(results=results)
