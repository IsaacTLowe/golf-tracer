from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from tracer_core import db, media, synth
from tracer_spec.render import draw_tracer
from tracer_worker.main import Worker

pytestmark = [pytest.mark.db, pytest.mark.media]

if shutil.which(media.ffmpeg_bin()) is None:
    pytest.skip("ffmpeg not available", allow_module_level=True)


@pytest.fixture(scope="session")
def swing_clip(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    out = tmp_path_factory.mktemp("synth")
    params = synth.SynthParams(width=540, height=960, fps=60, duration_s=5.0, impact_s=2.0, seed=7)
    video, labels = synth.generate(out, "swing", params)
    return video, labels.__dict__


def _upload(api: Any, user: dict[str, str], local_storage: Any, data: bytes, name: str = "swing.mp4") -> str:
    resp = api.post(
        "/v1/clips",
        json={"filename": name, "size_bytes": len(data), "content_type": "video/mp4", "duration_s": 5.0},
        headers={"Authorization": user["auth"]},
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    dest = local_storage.root / "raw" / created["upload"]["object_path"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    resp = api.post(f"/v1/clips/{created['clip']['id']}/complete", headers={"Authorization": user["auth"]})
    assert resp.status_code == 200, resp.text
    clip_id: str = created["clip"]["id"]
    return clip_id


def _run_all(worker: Worker, database_url: str) -> int:
    n = 0
    with db.connect(database_url) as conn:
        while worker.run_one(conn):
            n += 1
    return n


def test_process_manual_trace_approve_render(
    api: Any, make_user: Any, local_storage: Any, settings: Any, database_url: str, swing_clip: Any, tmp_path: Path
) -> None:
    video, labels = swing_clip
    user = make_user()
    h = {"Authorization": user["auth"]}
    clip_id = _upload(api, user, local_storage, video.read_bytes())
    settings.work_dir = str(tmp_path)
    worker = Worker(settings, local_storage, worker_id="test")
    assert _run_all(worker, database_url) == 1

    detail = api.get(f"/v1/clips/{clip_id}", headers=h).json()
    assert detail["status"] in ("review", "needs_input"), detail
    assert (detail["width"], detail["height"], detail["fps"]) == (540, 960, 60.0)
    assert detail["playback_url"] and detail["thumbnail_url"]
    norm = local_storage.root / "media" / user["id"] / clip_id / "normalized.mp4"
    assert norm.exists() and (norm.parent / "thumb.jpg").exists()
    assert detail["detection"]["impact_time_s"] == pytest.approx(labels["impact_time_s"], abs=2.5 / 60)
    if detail["status"] == "review":
        assert detail["tracer"]["source"] == "auto"
        assert detail["tracer"]["data"]["fit"]["method"] == "image_v1"

    # Manual tracer from the wizard's three taps, approve, export.
    a = labels["address"]
    resp = api.post(
        f"/v1/clips/{clip_id}/tracer/manual",
        json={
            "impact_time_s": labels["impact_time_s"],
            "start": [a["x"], a["y"]],
            "apex": [0.52, 0.33],
            "landing": [0.53, 0.53],
        },
        headers=h,
    )
    assert resp.status_code == 201, resp.text
    tracer = resp.json()["data"]
    version = resp.json()["version"]
    assert api.post(f"/v1/clips/{clip_id}/approve", headers=h).status_code == 200
    assert api.post(f"/v1/clips/{clip_id}/render", headers=h).json()["reused"] is False
    assert _run_all(worker, database_url) == 1
    link = api.get(f"/v1/clips/{clip_id}/render", headers=h).json()
    assert link["tracer_version"] == version and link["current"]
    detail = api.get(f"/v1/clips/{clip_id}", headers=h).json()
    assert detail["status"] == "approved"

    # The render matches the reference drawing of the same frame (up to H.264 loss).
    rendered = local_storage.root / "renders" / user["id"] / clip_id / f"render_v{version}.mp4"
    p = media.probe(rendered)
    assert (p.width, p.height) == (540, 960) and p.has_audio
    frame_i = int((tracer["landing_time_s"] - 0.2) * 60)
    src = _frame(norm, frame_i)
    out = _frame(rendered, frame_i)
    expected = draw_tracer(src, tracer, frame_i / 60)
    diff = np.abs(out.astype(int) - expected.astype(int))
    assert diff.mean() < 2.5
    # ...and the tracer is really there.
    assert np.abs(out.astype(int) - src.astype(int)).max() > 100


def _frame(path: Path, index: int) -> np.ndarray:
    cap = cv2.VideoCapture(str(path))
    for _ in range(index):
        cap.grab()
    ok, frame = cap.read()
    cap.release()
    assert ok
    return frame


def test_unreadable_file_fails_without_retry(
    api: Any, make_user: Any, local_storage: Any, settings: Any, database_url: str, tmp_path: Path
) -> None:
    user = make_user()
    clip_id = _upload(api, user, local_storage, b"this is not a video" * 100)
    settings.work_dir = str(tmp_path)
    worker = Worker(settings, local_storage, worker_id="test")
    assert _run_all(worker, database_url) == 1
    detail = api.get(f"/v1/clips/{clip_id}", headers={"Authorization": user["auth"]}).json()
    assert detail["status"] == "failed"
    assert detail["error_code"] == "unreadable"
    with db.transaction() as conn:
        job = conn.execute("select status, attempts from public.jobs where clip_id = %s", (clip_id,)).fetchone()
    assert job == {"status": "failed", "attempts": 1}
    # Manual retry from the UI re-queues processing.
    resp = api.post(f"/v1/clips/{clip_id}/retrace", headers={"Authorization": user["auth"]})
    assert resp.status_code == 200 and resp.json()["status"] == "queued"


def test_clip_deleted_before_job_runs_is_dropped(
    api: Any, make_user: Any, local_storage: Any, settings: Any, database_url: str, swing_clip: Any, tmp_path: Path
) -> None:
    video, _ = swing_clip
    user = make_user()
    clip_id = _upload(api, user, local_storage, video.read_bytes())
    with db.transaction() as conn:
        # Simulate a delete racing the worker: the clip disappears after the job was claimed.
        conn.execute("update public.jobs set payload = '{}' where clip_id = %s", (clip_id,))
    settings.work_dir = str(tmp_path)
    worker = Worker(settings, local_storage, worker_id="test")
    with db.connect(database_url) as conn:
        from tracer_core import jobs

        job = jobs.claim(conn, "test")
        assert job is not None
        assert api.delete(f"/v1/clips/{clip_id}", headers={"Authorization": user["auth"]}).status_code == 204
        from tracer_worker.handlers import ClipGone, handle_process

        with pytest.raises(ClipGone):
            handle_process(worker.ctx, job)
