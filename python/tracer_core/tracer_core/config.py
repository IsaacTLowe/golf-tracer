"""Settings shared by the API and the worker, read from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = "http://127.0.0.1:54321"
    # URL the *browser* uses to reach Supabase; differs from supabase_url inside Docker.
    supabase_public_url: str | None = None
    supabase_service_role_key: str = ""
    supabase_jwt_audience: str = "authenticated"
    # Optional legacy HS256 secret. Asymmetric keys via JWKS are always tried first.
    supabase_jwt_secret: str | None = None
    database_url: str = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"

    trace_debug: bool = False
    watermark: bool = False
    app_name: str = "Golf Tracer"

    signed_url_ttl_s: int = 3600
    clip_rate_limit_per_hour: int = 200
    max_batch_size: int = 50
    max_clip_bytes: int = 500 * 1024 * 1024
    min_duration_s: float = 2.0
    max_duration_s: float = 60.0
    min_short_edge_px: int = 480

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"])

    # Worker
    worker_id: str | None = None
    job_lock_timeout_s: int = 600
    job_heartbeat_s: int = 30
    job_poll_s: float = 5.0
    work_dir: str | None = None

    # Retention (milestone 7)
    abandoned_upload_hours: int = 24
    raw_retention_days_after_approval: int = 30

    @property
    def public_supabase_url(self) -> str:
        return self.supabase_public_url or self.supabase_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
