"""50-clip batch load test (milestone 7).

Signs up a throwaway user, uploads N copies of a clip as one batch through the same path the
clients use (POST /v1/batches → POST /v1/clips → TUS upload to Storage → complete) with 3
concurrent uploads, then waits until every clip has been processed and reports timings.

Usage:
  uv run python tools/loadtest/batch_load.py --clip path/to/swing.mp4 --count 50 \\
      --api http://localhost:8000 --supabase http://127.0.0.1:54321 --anon-key <key>
"""

from __future__ import annotations

import argparse
import base64
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

DONE = {"review", "needs_input", "failed", "approved"}


def b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def tus_upload(client: httpx.Client, target: dict, token: str, anon: str, data: bytes) -> None:
    headers = {
        "authorization": f"Bearer {token}",
        "apikey": anon,
        "x-upsert": "true",
        "Tus-Resumable": "1.0.0",
        "Upload-Length": str(len(data)),
        "Upload-Metadata": ",".join(
            f"{k} {b64(v)}"
            for k, v in {
                "bucketName": target["bucket"],
                "objectName": target["object_path"],
                "contentType": target["content_type"],
                "cacheControl": "3600",
            }.items()
        ),
    }
    r = client.post(target["tus_endpoint"], headers=headers)
    r.raise_for_status()
    location = r.headers["Location"]
    offset, chunk = 0, target["chunk_size"]
    while offset < len(data):
        body = data[offset : offset + chunk]
        r = client.patch(
            location,
            content=body,
            headers={
                "authorization": f"Bearer {token}",
                "apikey": anon,
                "Tus-Resumable": "1.0.0",
                "Upload-Offset": str(offset),
                "Content-Type": "application/offset+octet-stream",
            },
        )
        r.raise_for_status()
        offset = int(r.headers["Upload-Offset"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", type=Path, required=True)
    ap.add_argument("--count", type=int, default=50)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--supabase", default="http://127.0.0.1:54321")
    ap.add_argument("--anon-key", required=True)
    ap.add_argument("--timeout", type=float, default=1800)
    args = ap.parse_args()
    data = args.clip.read_bytes()
    client = httpx.Client(timeout=120)

    email = f"load-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post(
        f"{args.supabase}/auth/v1/signup",
        json={"email": email, "password": "load-test-password-1"},
        headers={"apikey": args.anon_key},
    )
    r.raise_for_status()
    token = r.json()["access_token"]
    api = httpx.Client(base_url=args.api, headers={"Authorization": f"Bearer {token}"}, timeout=60)

    t0 = time.perf_counter()
    batch = api.post("/v1/batches", json={"clip_count": args.count}).raise_for_status().json()

    def one(i: int) -> float:
        start = time.perf_counter()
        created = api.post(
            "/v1/clips",
            json={
                "filename": f"load_{i:02d}{args.clip.suffix}",
                "size_bytes": len(data),
                "content_type": "video/mp4",
                "batch_id": batch["id"],
            },
        )
        created.raise_for_status()
        c = created.json()
        tus_upload(client, c["upload"], token, args.anon_key, data)
        api.post(f"/v1/clips/{c['clip']['id']}/complete").raise_for_status()
        return time.perf_counter() - start

    with ThreadPoolExecutor(max_workers=3) as pool:
        upload_times = list(pool.map(one, range(args.count)))
    t_uploaded = time.perf_counter() - t0
    print(f"uploaded {args.count} clips in {t_uploaded:.1f}s (per clip p50 {statistics.median(upload_times):.2f}s)")

    statuses: dict[str, int] = {}
    while time.perf_counter() - t0 < args.timeout:
        items = api.get("/v1/clips", params={"batch_id": batch["id"], "limit": 100}).raise_for_status().json()["items"]
        statuses = {}
        for it in items:
            statuses[it["status"]] = statuses.get(it["status"], 0) + 1
        if sum(v for k, v in statuses.items() if k in DONE) >= args.count:
            break
        time.sleep(2)
    total = time.perf_counter() - t0
    print(f"all processed after {total:.1f}s: {statuses}")
    done = sum(v for k, v in statuses.items() if k in DONE)
    if done < args.count or statuses.get("failed"):
        print("LOAD TEST FAILED", file=sys.stderr)
        return 1
    print(f"throughput: {args.count / (total - t_uploaded + 1e-9):.2f} clips/s after uploads finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
