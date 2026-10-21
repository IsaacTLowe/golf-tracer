"""Supabase Storage access for the API and worker (service role).

Video bytes never pass through the API: it only signs URLs, checks objects exist and deletes
them. The worker downloads raw clips and uploads normalized media and renders.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx

RAW = "raw"
MEDIA = "media"
RENDERS = "renders"


def raw_path(user_id: str, clip_id: str, ext: str) -> str:
    return f"{user_id}/{clip_id}/original.{ext}"


def normalized_path(user_id: str, clip_id: str) -> str:
    return f"{user_id}/{clip_id}/normalized.mp4"


def thumbnail_path(user_id: str, clip_id: str) -> str:
    return f"{user_id}/{clip_id}/thumb.jpg"


def debug_path(user_id: str, clip_id: str) -> str:
    return f"{user_id}/{clip_id}/debug.mp4"


def render_path(user_id: str, clip_id: str, version: int) -> str:
    return f"{user_id}/{clip_id}/render_v{version}.mp4"


class StorageError(RuntimeError):
    pass


class Storage(Protocol):
    def object_size(self, bucket: str, path: str) -> int | None: ...
    def sign(self, bucket: str, path: str, expires_in: int, *, download: str | None = None) -> str: ...
    def sign_many(self, bucket: str, paths: list[str], expires_in: int) -> dict[str, str]: ...
    def download_to(self, bucket: str, path: str, dest: Path) -> None: ...
    def upload_file(self, bucket: str, path: str, src: Path, content_type: str) -> None: ...
    def delete(self, bucket: str, paths: list[str]) -> None: ...
    def list_prefix(self, bucket: str, prefix: str) -> list[str]: ...


def _quote_path(path: str) -> str:
    return quote(path, safe="/")


class SupabaseStorage:
    def __init__(self, base_url: str, service_key: str, public_url: str | None = None, timeout: float = 60.0):
        self.base = base_url.rstrip("/") + "/storage/v1"
        self.public_base = (public_url or base_url).rstrip("/") + "/storage/v1"
        headers = {"apikey": service_key}
        if service_key.count(".") == 2:  # legacy JWT-style keys also go in Authorization
            headers["Authorization"] = f"Bearer {service_key}"
        self.client = httpx.Client(
            headers=headers,
            timeout=httpx.Timeout(timeout, read=600.0, write=600.0),
        )

    def _raise(self, resp: httpx.Response, what: str) -> None:
        if resp.status_code >= 400:
            raise StorageError(f"{what} failed: {resp.status_code} {resp.text[:300]}")

    def object_size(self, bucket: str, path: str) -> int | None:
        resp = self.client.head(f"{self.base}/object/{bucket}/{_quote_path(path)}")
        if resp.status_code in (400, 404):
            return None
        self._raise(resp, "head")
        length = resp.headers.get("content-length")
        return int(length) if length is not None else 0

    def sign(self, bucket: str, path: str, expires_in: int, *, download: str | None = None) -> str:
        resp = self.client.post(f"{self.base}/object/sign/{bucket}/{_quote_path(path)}", json={"expiresIn": expires_in})
        self._raise(resp, "sign")
        url: str = self.public_base + resp.json()["signedURL"]
        if download is not None:
            url += "&download=" + quote(download)
        return url

    def sign_many(self, bucket: str, paths: list[str], expires_in: int) -> dict[str, str]:
        if not paths:
            return {}
        resp = self.client.post(f"{self.base}/object/sign/{bucket}", json={"expiresIn": expires_in, "paths": paths})
        self._raise(resp, "sign_many")
        return {
            item["path"]: self.public_base + item["signedURL"]
            for item in resp.json()
            if item.get("signedURL") and not item.get("error")
        }

    def download_to(self, bucket: str, path: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.client.stream("GET", f"{self.base}/object/{bucket}/{_quote_path(path)}") as resp:
            if resp.status_code >= 400:
                resp.read()
                self._raise(resp, "download")
            with dest.open("wb") as f:
                for chunk in resp.iter_bytes(1 << 20):
                    f.write(chunk)

    def upload_file(self, bucket: str, path: str, src: Path, content_type: str) -> None:
        with src.open("rb") as f:
            resp = self.client.post(
                f"{self.base}/object/{bucket}/{_quote_path(path)}",
                content=f,
                headers={"Content-Type": content_type, "x-upsert": "true", "cache-control": "3600"},
            )
        self._raise(resp, "upload")

    def delete(self, bucket: str, paths: list[str]) -> None:
        for i in range(0, len(paths), 100):
            resp = self.client.request("DELETE", f"{self.base}/object/{bucket}", json={"prefixes": paths[i : i + 100]})
            self._raise(resp, "delete")

    def list_prefix(self, bucket: str, prefix: str) -> list[str]:
        resp = self.client.post(
            f"{self.base}/object/list/{bucket}", json={"prefix": prefix.rstrip("/"), "limit": 1000, "offset": 0}
        )
        self._raise(resp, "list")
        base = prefix.rstrip("/")
        return [f"{base}/{item['name']}" for item in resp.json() if item.get("id")]


class LocalStorage:
    """Filesystem stand-in used by tests and offline development."""

    def __init__(self, root: Path, public_base: str = "http://storage.local"):
        self.root = root
        self.public_base = public_base

    def _p(self, bucket: str, path: str) -> Path:
        return self.root / bucket / path

    def object_size(self, bucket: str, path: str) -> int | None:
        p = self._p(bucket, path)
        return p.stat().st_size if p.is_file() else None

    def sign(self, bucket: str, path: str, expires_in: int, *, download: str | None = None) -> str:
        url = f"{self.public_base}/{bucket}/{path}?expires={expires_in}"
        return url + (f"&download={quote(download)}" if download else "")

    def sign_many(self, bucket: str, paths: list[str], expires_in: int) -> dict[str, str]:
        return {p: self.sign(bucket, p, expires_in) for p in paths if self._p(bucket, p).is_file()}

    def download_to(self, bucket: str, path: str, dest: Path) -> None:
        src = self._p(bucket, path)
        if not src.is_file():
            raise StorageError(f"download failed: {bucket}/{path} not found")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)

    def upload_file(self, bucket: str, path: str, src: Path, content_type: str) -> None:
        dest = self._p(bucket, path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)

    def delete(self, bucket: str, paths: list[str]) -> None:
        for p in paths:
            self._p(bucket, p).unlink(missing_ok=True)

    def list_prefix(self, bucket: str, prefix: str) -> list[str]:
        base = self._p(bucket, prefix)
        if not base.is_dir():
            return []
        return [f"{prefix.rstrip('/')}/{f.name}" for f in base.iterdir() if f.is_file()]


def from_settings() -> Storage:
    from .config import get_settings

    s = get_settings()
    return SupabaseStorage(s.supabase_url, s.supabase_service_role_key, s.public_supabase_url)
