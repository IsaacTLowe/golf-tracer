from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tracer_core import db, storage

pytestmark = pytest.mark.db


def create_clip(api: Any, user: dict[str, str], **overrides: Any) -> dict[str, Any]:
    body = {"filename": "swing.mp4", "size_bytes": 1000, "content_type": "video/mp4", "duration_s": 8.0}
    body.update(overrides)
    resp = api.post("/v1/clips", json=body, headers={"Authorization": user["auth"]})
    assert resp.status_code == 201, resp.text
    data: dict[str, Any] = resp.json()
    return data


def put_raw(local_storage: Any, path: str, data: bytes = b"x" * 1000) -> None:
    p: Path = local_storage.root / "raw" / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def make_processed(clip_id: str, user_id: str, status: str = "needs_input") -> None:
    with db.transaction() as conn:
        conn.execute(
            """
            update public.clips set status = %s, normalized_path = %s, thumbnail_path = %s,
                   width = 1080, height = 1920, fps = 60, duration_s = 8.0
             where id = %s
            """,
            (status, storage.normalized_path(user_id, clip_id), storage.thumbnail_path(user_id, clip_id), clip_id),
        )


MANUAL = {"impact_time_s": 2.0, "start": [0.5, 0.8], "apex": [0.52, 0.2], "landing": [0.56, 0.42]}


# --------------------------------------------------------------------------------------------
# Health and auth
# --------------------------------------------------------------------------------------------
def test_health_needs_no_auth(api: Any) -> None:
    resp = api.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["database"] == "ok"


def test_missing_token_is_401_with_error_envelope(api: Any) -> None:
    resp = api.get("/v1/clips")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_expired_token_is_401(api: Any, make_user: Any, make_token: Any) -> None:
    user = make_user()
    token = make_token(user["id"], exp_in=-10)
    resp = api.get("/v1/clips", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "token_expired"


def test_wrong_audience_is_401(api: Any, make_user: Any, make_token: Any) -> None:
    user = make_user()
    token = make_token(user["id"], aud="something-else")
    resp = api.get("/v1/clips", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_audience"


def test_garbage_token_is_401(api: Any) -> None:
    resp = api.get("/v1/clips", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401


def test_token_signed_by_another_key_is_401(api: Any, make_user: Any) -> None:
    import time

    import jwt
    from cryptography.hazmat.primitives.asymmetric import ec

    user = make_user()
    other = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(
        {"sub": user["id"], "aud": "authenticated", "exp": int(time.time()) + 60, "role": "authenticated"},
        other,
        algorithm="ES256",
    )
    resp = api.get("/v1/clips", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


# --------------------------------------------------------------------------------------------
# Upload flow
# --------------------------------------------------------------------------------------------
def test_upload_flow_create_complete_enqueues_process(api: Any, make_user: Any, local_storage: Any) -> None:
    user = make_user()
    created = create_clip(api, user)
    clip, upload = created["clip"], created["upload"]
    assert clip["status"] == "uploading"
    assert upload["bucket"] == "raw"
    assert upload["object_path"] == f"{user['id']}/{clip['id']}/original.mp4"
    assert upload["tus_endpoint"].endswith("/storage/v1/upload/resumable")
    assert upload["chunk_size"] == 6 * 1024 * 1024

    resp = api.post(f"/v1/clips/{clip['id']}/complete", headers={"Authorization": user["auth"]})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "upload_missing"

    put_raw(local_storage, upload["object_path"])
    resp = api.post(f"/v1/clips/{clip['id']}/complete", headers={"Authorization": user["auth"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "queued"
    # Idempotent.
    resp = api.post(f"/v1/clips/{clip['id']}/complete", headers={"Authorization": user["auth"]})
    assert resp.status_code == 200
    with db.transaction() as conn:
        jobs = conn.execute("select kind, status from public.jobs where clip_id = %s", (clip["id"],)).fetchall()
    assert jobs == [{"kind": "process", "status": "queued"}]


@pytest.mark.parametrize(
    ("overrides", "status", "code"),
    [
        ({"size_bytes": 600 * 1024 * 1024}, 413, "file_too_large"),
        ({"duration_s": 61.0}, 422, "too_long"),
        ({"duration_s": 1.0}, 422, "too_short"),
        ({"filename": "swing.avi", "content_type": "video/x-msvideo"}, 415, "unsupported_type"),
        ({"width": 640, "height": 360}, 422, "resolution_too_low"),
    ],
)
def test_create_clip_rejects_out_of_limits(
    api: Any, make_user: Any, overrides: dict[str, Any], status: int, code: str
) -> None:
    user = make_user()
    body = {"filename": "swing.mp4", "size_bytes": 1000, "content_type": "video/mp4", "duration_s": 8.0, **overrides}
    resp = api.post("/v1/clips", json=body, headers={"Authorization": user["auth"]})
    assert resp.status_code == status, resp.text
    assert resp.json()["error"]["code"] == code


def test_hevc_without_duration_is_accepted(api: Any, make_user: Any) -> None:
    user = make_user()
    created = create_clip(api, user, filename="IMG_0001.MOV", content_type="video/quicktime", duration_s=None)
    assert created["upload"]["object_path"].endswith("original.mov")


def test_batches(api: Any, make_user: Any) -> None:
    user = make_user()
    resp = api.post("/v1/batches", json={"clip_count": 3}, headers={"Authorization": user["auth"]})
    assert resp.status_code == 201
    batch_id = resp.json()["id"]
    for _ in range(3):
        create_clip(api, user, batch_id=batch_id)
    other = make_user()
    create_clip(api, other)
    page = api.get(f"/v1/clips?batch_id={batch_id}", headers={"Authorization": user["auth"]}).json()
    assert len(page["items"]) == 3
    # A batch belonging to someone else can't be used.
    resp = api.post(
        "/v1/clips",
        json={"filename": "a.mp4", "size_bytes": 10, "content_type": "video/mp4", "batch_id": batch_id},
        headers={"Authorization": other["auth"]},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "batch_not_found"
    resp = api.post("/v1/batches", json={"clip_count": 51}, headers={"Authorization": user["auth"]})
    assert resp.status_code == 422


def test_rate_limit_returns_429_with_retry_after(api: Any, make_user: Any, settings: Any) -> None:
    settings.clip_rate_limit_per_hour = 3
    user = make_user()
    for _ in range(3):
        create_clip(api, user)
    resp = api.post(
        "/v1/clips",
        json={"filename": "a.mp4", "size_bytes": 10, "content_type": "video/mp4"},
        headers={"Authorization": user["auth"]},
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"
    assert int(resp.headers["Retry-After"]) > 0


# --------------------------------------------------------------------------------------------
# Listing and ownership
# --------------------------------------------------------------------------------------------
def test_list_pagination_and_status_filter(api: Any, make_user: Any) -> None:
    user = make_user()
    ids = [create_clip(api, user, filename=f"c{i}.mp4")["clip"]["id"] for i in range(5)]
    make_processed(ids[0], user["id"])
    h = {"Authorization": user["auth"]}
    page1 = api.get("/v1/clips?limit=2", headers=h).json()
    assert len(page1["items"]) == 2 and page1["next_cursor"]
    page2 = api.get(f"/v1/clips?limit=2&cursor={page1['next_cursor']}", headers=h).json()
    page3 = api.get(f"/v1/clips?limit=2&cursor={page2['next_cursor']}", headers=h).json()
    seen = [c["id"] for p in (page1, page2, page3) for c in p["items"]]
    assert sorted(seen) == sorted(ids)
    assert page3["next_cursor"] is None
    assert seen == sorted(seen, key=lambda i: ids.index(i), reverse=True)  # newest first

    needs = api.get("/v1/clips?status=needs_input", headers=h).json()["items"]
    assert [c["id"] for c in needs] == [ids[0]]
    both = api.get("/v1/clips?status=needs_input,uploading", headers=h).json()["items"]
    assert len(both) == 5
    assert api.get("/v1/clips?cursor=garbage", headers=h).status_code == 400


def test_users_cannot_see_or_touch_each_others_clips(api: Any, make_user: Any) -> None:
    alice, bob = make_user(), make_user()
    clip_id = create_clip(api, alice)["clip"]["id"]
    make_processed(clip_id, alice["id"])
    hb = {"Authorization": bob["auth"]}
    assert api.get("/v1/clips", headers=hb).json()["items"] == []
    for method, path, body in [
        ("get", f"/v1/clips/{clip_id}", None),
        ("delete", f"/v1/clips/{clip_id}", None),
        ("post", f"/v1/clips/{clip_id}/complete", None),
        ("post", f"/v1/clips/{clip_id}/approve", None),
        ("post", f"/v1/clips/{clip_id}/retrace", {}),
        ("post", f"/v1/clips/{clip_id}/tracer/manual", MANUAL),
        ("get", f"/v1/clips/{clip_id}/tracer/versions", None),
        ("post", f"/v1/clips/{clip_id}/render", None),
        ("get", f"/v1/clips/{clip_id}/render", None),
    ]:
        resp = api.request(method.upper(), path, json=body, headers=hb)
        assert resp.status_code == 404, (method, path, resp.text)
    assert api.get(f"/v1/clips/{clip_id}", headers={"Authorization": alice["auth"]}).status_code == 200
    assert api.get("/v1/clips/not-a-uuid", headers=hb).status_code == 404


def test_delete_removes_row_and_files(api: Any, make_user: Any, local_storage: Any) -> None:
    user = make_user()
    created = create_clip(api, user)
    clip_id = created["clip"]["id"]
    put_raw(local_storage, created["upload"]["object_path"])
    for bucket, name in (("media", "normalized.mp4"), ("media", "thumb.jpg"), ("renders", "render_v1.mp4")):
        p = local_storage.root / bucket / user["id"] / clip_id / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"1")
    resp = api.delete(f"/v1/clips/{clip_id}", headers={"Authorization": user["auth"]})
    assert resp.status_code == 204
    assert not [
        f for b in ("raw", "media", "renders") for f in (local_storage.root / b / user["id"] / clip_id).glob("*")
    ]
    assert api.get(f"/v1/clips/{clip_id}", headers={"Authorization": user["auth"]}).status_code == 404


# --------------------------------------------------------------------------------------------
# Tracing, versions, approval, render
# --------------------------------------------------------------------------------------------
def test_manual_tracer_edit_versions_approve_render(api: Any, make_user: Any, local_storage: Any) -> None:
    user = make_user()
    h = {"Authorization": user["auth"]}
    clip_id = create_clip(api, user)["clip"]["id"]

    # Not processed yet.
    resp = api.post(f"/v1/clips/{clip_id}/tracer/manual", json=MANUAL, headers=h)
    assert resp.status_code == 409

    make_processed(clip_id, user["id"])
    resp = api.post(f"/v1/clips/{clip_id}/tracer/manual", json=MANUAL, headers=h)
    assert resp.status_code == 201, resp.text
    t1 = resp.json()
    assert t1["version"] == 1 and t1["source"] == "manual"
    doc = t1["data"]
    assert doc["video"] == {"width": 1080, "height": 1920, "fps": 60.0, "duration_s": 8.0}
    assert doc["path"]["c1"][1] == doc["path"]["apex"][1] == doc["path"]["c2"][1]
    assert doc["impact_time_s"] < doc["apex_time_s"] < doc["landing_time_s"] <= 8.0

    detail = api.get(f"/v1/clips/{clip_id}", headers=h).json()
    assert detail["status"] == "review"
    assert detail["tracer"]["version"] == 1
    assert detail["playback_url"]

    # Edit: new version; stale base version conflicts.
    doc["style"]["color"] = "#FFFFFF"
    resp = api.put(f"/v1/clips/{clip_id}/tracer", json={"base_version": 1, "data": doc}, headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == 2 and resp.json()["source"] == "edited"
    resp = api.put(f"/v1/clips/{clip_id}/tracer", json={"base_version": 1, "data": doc}, headers=h)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "version_conflict"

    # Invalid tracer rejected.
    bad = {**doc, "path": {**doc["path"], "c1": [0.4, 0.5]}}
    resp = api.put(f"/v1/clips/{clip_id}/tracer", json={"base_version": 2, "data": bad}, headers=h)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_tracer"

    versions = api.get(f"/v1/clips/{clip_id}/tracer/versions", headers=h).json()["items"]
    assert [v["version"] for v in versions] == [2, 1]

    # Render before approval is refused.
    assert api.post(f"/v1/clips/{clip_id}/render", headers=h).status_code == 409

    resp = api.post(f"/v1/clips/{clip_id}/approve", headers=h)
    assert resp.status_code == 200 and resp.json()["status"] == "approved"

    resp = api.post(f"/v1/clips/{clip_id}/render", headers=h)
    assert resp.status_code == 200
    assert resp.json()["reused"] is False
    assert resp.json()["clip"]["status"] == "rendering"
    with db.transaction() as conn:
        job = conn.execute(
            "select payload from public.jobs where clip_id = %s and kind = 'render'", (clip_id,)
        ).fetchone()
    assert job == {"payload": {"tracer_version": 2}}
    assert api.get(f"/v1/clips/{clip_id}/render", headers=h).status_code == 404

    # Simulate the worker finishing.
    rpath = storage.render_path(user["id"], clip_id, 2)
    (local_storage.root / "renders" / rpath).parent.mkdir(parents=True, exist_ok=True)
    (local_storage.root / "renders" / rpath).write_bytes(b"mp4")
    with db.transaction() as conn:
        conn.execute("update public.clips set status = 'approved', render_path = %s where id = %s", (rpath, clip_id))
    link = api.get(f"/v1/clips/{clip_id}/render", headers=h).json()
    assert link["tracer_version"] == 2 and link["current"] is True and "swing-traced.mp4" in link["url"]

    # Unchanged tracer: reuse the existing file.
    resp = api.post(f"/v1/clips/{clip_id}/render", headers=h)
    assert resp.json()["reused"] is True

    # Editing an approved clip sends it back to review and marks the render stale.
    resp = api.put(f"/v1/clips/{clip_id}/tracer", json={"base_version": 2, "data": doc}, headers=h)
    assert resp.status_code == 200
    detail = api.get(f"/v1/clips/{clip_id}", headers=h).json()
    assert detail["status"] == "review" and detail["approved_at"] is None
    link = api.get(f"/v1/clips/{clip_id}/render", headers=h).json()
    assert link["current"] is False


def test_manual_tracer_validation(api: Any, make_user: Any) -> None:
    user = make_user()
    h = {"Authorization": user["auth"]}
    clip_id = create_clip(api, user)["clip"]["id"]
    make_processed(clip_id, user["id"])
    resp = api.post(f"/v1/clips/{clip_id}/tracer/manual", json={**MANUAL, "impact_time_s": 9.0}, headers=h)
    assert resp.status_code == 422
    resp = api.post(
        f"/v1/clips/{clip_id}/tracer/manual", json={**MANUAL, "apex": [0.5, 0.805], "landing": [0.55, 0.81]}, headers=h
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "apex_too_low"
    resp = api.post(f"/v1/clips/{clip_id}/tracer/manual", json={**MANUAL, "start": [1.5, 0.8]}, headers=h)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_manual_tracer_uses_profile_default_style(api: Any, make_user: Any) -> None:
    user = make_user()
    h = {"Authorization": user["auth"]}
    style = {"color": "#0A84FF", "width": 0.01, "taper": 0.5, "glow": 0.2, "persist_s": None}
    resp = api.patch("/v1/me", json={"default_tracer_style": style}, headers=h)
    assert resp.status_code == 200 and resp.json()["default_tracer_style"] == style
    clip_id = create_clip(api, user)["clip"]["id"]
    make_processed(clip_id, user["id"])
    t = api.post(f"/v1/clips/{clip_id}/tracer/manual", json=MANUAL, headers=h).json()
    assert t["data"]["style"] == style
    # Display name update leaves the style alone.
    resp = api.patch("/v1/me", json={"display_name": "Pat"}, headers=h)
    assert resp.json()["display_name"] == "Pat" and resp.json()["default_tracer_style"] == style


def test_retrace_and_manual_retry_of_failed(api: Any, make_user: Any) -> None:
    user = make_user()
    h = {"Authorization": user["auth"]}
    clip_id = create_clip(api, user)["clip"]["id"]
    assert api.post(f"/v1/clips/{clip_id}/retrace", json={}, headers=h).status_code == 409  # still uploading
    make_processed(clip_id, user["id"], status="review")
    resp = api.post(f"/v1/clips/{clip_id}/retrace", json={"ball_xy": [0.5, 0.8], "impact_time_s": 2.5}, headers=h)
    assert resp.status_code == 200 and resp.json()["status"] == "queued"
    with db.transaction() as conn:
        job = conn.execute("select kind, payload from public.jobs where clip_id = %s", (clip_id,)).fetchone()
    assert job == {"kind": "retrace", "payload": {"ball_xy": [0.5, 0.8], "impact_time_s": 2.5}}

    # A clip that failed before normalization restarts from `process`.
    with db.transaction() as conn:
        conn.execute(
            "update public.clips set status = 'failed', error_code = 'unreadable', normalized_path = null"
            " where id = %s",
            (clip_id,),
        )
    resp = api.post(f"/v1/clips/{clip_id}/retrace", headers=h)
    assert resp.status_code == 200 and resp.json()["error_code"] is None
    with db.transaction() as conn:
        kinds = [
            r["kind"]
            for r in conn.execute("select kind from public.jobs where clip_id = %s and status = 'queued'", (clip_id,))
        ]
    assert "process" in kinds


def test_bulk_actions(api: Any, make_user: Any) -> None:
    user = make_user()
    h = {"Authorization": user["auth"]}
    ids = [create_clip(api, user)["clip"]["id"] for _ in range(3)]
    for cid in ids[:2]:
        make_processed(cid, user["id"])
        assert api.post(f"/v1/clips/{cid}/tracer/manual", json=MANUAL, headers=h).status_code == 201
    resp = api.post("/v1/clips/bulk", json={"action": "approve", "clip_ids": ids}, headers=h)
    results = {r["clip_id"]: r for r in resp.json()["results"]}
    assert results[ids[0]]["ok"] and results[ids[1]]["ok"]
    assert not results[ids[2]]["ok"] and results[ids[2]]["error"] == "not_reviewable"
    resp = api.post("/v1/clips/bulk", json={"action": "render", "clip_ids": ids[:2]}, headers=h)
    assert all(r["ok"] for r in resp.json()["results"])
    resp = api.post("/v1/clips/bulk", json={"action": "delete", "clip_ids": ids}, headers=h)
    assert all(r["ok"] for r in resp.json()["results"])
    assert api.get("/v1/clips", headers=h).json()["items"] == []
    resp = api.post("/v1/clips/bulk", json={"action": "delete", "clip_ids": [ids[0]] * 51}, headers=h)
    assert resp.status_code == 422


# --------------------------------------------------------------------------------------------
# Row Level Security (what a client using the anon key + user JWT can do directly)
# --------------------------------------------------------------------------------------------
def _as_user(conn: Any, user_id: str) -> None:
    import json

    conn.execute("set local role authenticated")
    conn.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps({"sub": user_id}),))


def test_rls_users_read_only_their_own_rows(api: Any, make_user: Any) -> None:
    alice, bob = make_user(), make_user()
    a_clip = create_clip(api, alice)["clip"]["id"]
    create_clip(api, bob)
    make_processed(a_clip, alice["id"])
    api.post(f"/v1/clips/{a_clip}/tracer/manual", json=MANUAL, headers={"Authorization": alice["auth"]})
    with db.transaction() as conn:
        _as_user(conn, bob["id"])
        assert conn.execute("select count(*) as n from public.clips").fetchone() == {"n": 1}
        assert conn.execute("select count(*) as n from public.tracers").fetchone() == {"n": 0}
        assert conn.execute("select count(*) as n from public.profiles").fetchone() == {"n": 1}
    with db.transaction() as conn:
        _as_user(conn, alice["id"])
        assert conn.execute("select count(*) as n from public.tracers").fetchone() == {"n": 1}


def test_rls_clients_cannot_write_or_see_jobs(api: Any, make_user: Any) -> None:
    import psycopg

    alice = make_user()
    clip_id = create_clip(api, alice)["clip"]["id"]
    for sql in (
        "select * from public.jobs",
        f"update public.clips set status = 'approved' where id = '{clip_id}'",
        f"insert into public.tracers (clip_id, version, source, data) values ('{clip_id}', 9, 'manual', '{{}}')",
        "insert into public.upload_batches (user_id, clip_count) values (auth.uid(), 1)",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction() as conn:
            _as_user(conn, alice["id"])
            conn.execute(sql)  # type: ignore[arg-type]


def test_rls_storage_upload_only_into_own_raw_folder(make_user: Any) -> None:
    import psycopg

    alice, bob = make_user(), make_user()
    with db.transaction() as conn:
        _as_user(conn, alice["id"])
        conn.execute(
            "insert into storage.objects (bucket_id, name) values ('raw', %s)", (f"{alice['id']}/c/original.mp4",)
        )
    for bucket, owner in (("raw", bob["id"]), ("media", alice["id"]), ("renders", alice["id"])):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction() as conn:
            _as_user(conn, alice["id"])
            conn.execute("insert into storage.objects (bucket_id, name) values (%s, %s)", (bucket, f"{owner}/c/x.mp4"))
