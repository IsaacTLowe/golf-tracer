"""Request and response models. The OpenAPI schema generated from these drives the typed
TypeScript client in packages/api-client."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

ClipStatus = Literal["uploading", "queued", "processing", "review", "needs_input", "approved", "rendering", "failed"]
TracerSource = Literal["auto", "manual", "edited"]

Unit = Annotated[float, Field(ge=0.0, le=1.0)]
Point = tuple[Unit, Unit]


# ----------------------------------------------------------------------------------------------
# Tracer document (mirror of packages/tracer-spec/schema/tracer.schema.json)
# ----------------------------------------------------------------------------------------------
class TracerVideo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    fps: float = Field(gt=0)
    duration_s: float = Field(gt=0)


class TracerPath(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: Point
    c1: Point
    apex: Point
    c2: Point
    landing: Point


class TracerTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ascent_ease: float = Field(ge=0.1, le=20)
    descent_ease: float = Field(ge=0.1, le=20)


class TracerStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    width: float = Field(gt=0, le=0.05)
    taper: float = Field(ge=0, le=1)
    glow: float = Field(ge=0, le=1)
    persist_s: float | None = Field(ge=0)


class ObservedPoint(BaseModel):
    t: float
    x: float
    y: float
    score: float | None = None


class TracerFit(BaseModel):
    model_config = ConfigDict(extra="allow")
    method: str | None = None
    residual_px: float | None = None
    timing_residual_px: float | None = None
    confidence: float | None = None


class TracerDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    video: TracerVideo
    impact_time_s: float = Field(ge=0)
    apex_time_s: float = Field(ge=0)
    landing_time_s: float = Field(ge=0)
    path: TracerPath
    timing: TracerTiming
    style: TracerStyle
    observed: list[ObservedPoint] | None = None
    fit: TracerFit | None = None


class TracerOut(BaseModel):
    id: str
    clip_id: str
    version: int
    source: TracerSource
    confidence: float | None
    created_at: datetime
    data: TracerDoc


# ----------------------------------------------------------------------------------------------
# Batches and clips
# ----------------------------------------------------------------------------------------------
class BatchCreate(BaseModel):
    clip_count: int = Field(ge=1, le=50)


class BatchOut(BaseModel):
    id: str
    clip_count: int
    created_at: datetime


class ClipCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    content_type: str = Field(min_length=1, max_length=100)
    duration_s: float | None = Field(default=None, ge=0, description="From the client's metadata read; null if unknown")
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    batch_id: str | None = None


class ClipOut(BaseModel):
    id: str
    batch_id: str | None
    original_filename: str
    status: ClipStatus
    error_code: str | None
    error_message: str | None
    duration_s: float | None
    fps: float | None
    width: int | None
    height: int | None
    size_bytes: int | None
    needs_check: bool
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime
    thumbnail_url: str | None = None
    current_tracer_version: int | None = None
    render_version: int | None = None


class ClipDetection(BaseModel):
    """What auto-tracing found; the manual wizard starts from these."""

    impact_time_s: float | None = None
    impact_source: str | None = None
    ball_xy: Point | None = None
    ball_r: float | None = None
    horizon_y: float | None = None
    confidence: float | None = None
    reason: str | None = None


class ClipDetail(ClipOut):
    playback_url: str | None = None
    debug_url: str | None = None
    tracer: TracerOut | None = None
    detection: ClipDetection | None = None


class UploadTarget(BaseModel):
    bucket: str
    object_path: str
    tus_endpoint: str = Field(description="Supabase Storage resumable (TUS) endpoint")
    chunk_size: int = Field(description="TUS chunk size Supabase requires, in bytes")
    content_type: str
    auth: Literal["user_access_token"] = Field(
        "user_access_token", description="Authenticate the TUS upload with the user's own Supabase access token"
    )


class ClipCreated(BaseModel):
    clip: ClipOut
    upload: UploadTarget


class ClipPage(BaseModel):
    items: list[ClipOut]
    next_cursor: str | None


class RetraceRequest(BaseModel):
    ball_xy: Point | None = Field(default=None, description="Ball position at address, normalized")
    impact_time_s: float | None = Field(default=None, ge=0)


class ManualTracerRequest(BaseModel):
    impact_time_s: float = Field(ge=0)
    start: Point
    apex: Point
    landing: Point
    curve: float | None = Field(default=None, ge=-1, le=1, description="Bend: negative left, positive right")
    landing_time_s: float | None = Field(default=None, ge=0)
    style: TracerStyle | None = None


class TracerUpdate(BaseModel):
    base_version: int = Field(ge=1)
    data: TracerDoc


class TracerVersions(BaseModel):
    items: list[TracerOut]


class RenderEnqueued(BaseModel):
    clip: ClipOut
    reused: bool = Field(description="True when an export of this tracer version already exists")


class RenderLink(BaseModel):
    url: str
    tracer_version: int
    current: bool = Field(description="False if the tracer was edited after this render")


class BulkRequest(BaseModel):
    action: Literal["approve", "render", "delete"]
    clip_ids: list[str] = Field(min_length=1, max_length=50)


class BulkResult(BaseModel):
    clip_id: str
    ok: bool
    error: str | None = None


class BulkResponse(BaseModel):
    results: list[BulkResult]


class ProfileOut(BaseModel):
    id: str
    display_name: str | None
    default_tracer_style: TracerStyle | None
    created_at: datetime


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    default_tracer_style: TracerStyle | None = None


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    database: Literal["ok", "unavailable"]
