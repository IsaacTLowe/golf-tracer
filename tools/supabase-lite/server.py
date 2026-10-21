"""supabase-lite: a tiny stand-in for the Supabase endpoints this app uses, for running the
whole stack (and the Playwright end-to-end tests) on a machine without Docker.

NOT for production and not a Supabase replacement. It implements just enough of:
  * Auth (GoTrue): email/password sign-up and sign-in, refresh, user, logout, JWKS (ES256)
  * Storage: TUS resumable uploads (user tokens, raw/{uid}/... only), object HEAD/GET/POST/
    DELETE/list and signed URLs (service key), signed downloads with HTTP range support
  * Realtime: postgres_changes on public.clips over the Phoenix v2 websocket protocol, with
    row ownership standing in for RLS

against a plain Postgres that has our migrations applied (with the Supabase stub schema from
tracer_core.testing). The real stack uses the Supabase CLI (`pnpm supabase:start`).

Run: uv run python tools/supabase-lite/server.py --db postgresql://postgres@127.0.0.1:54329/postgres
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import jwt
import psycopg
import uvicorn
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from psycopg.rows import dict_row

ANON_KEY = "anon-dev-key"
SERVICE_KEY = "service-role-dev-key"
JWT_TTL = 3600
TUS_HEADERS = {
    "Tus-Resumable": "1.0.0",
    "Tus-Version": "1.0.0",
    "Tus-Extension": "creation,creation-with-upload,termination",
}


class State:
    def __init__(self, db_url: str, root: Path, public_url: str):
        self.db_url = db_url
        self.root = root
        self.public_url = public_url.rstrip("/")
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.kid = secrets.token_hex(8)
        self.sign_secret = secrets.token_bytes(32)
        self.refresh: dict[str, str] = {}  # refresh token -> user id
        self.uploads: dict[str, dict[str, Any]] = {}
        self.sockets: set[RealtimeSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def db(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self.db_url, row_factory=dict_row, autocommit=True)


def b64url(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8 or 1, "big").rjust(32, b"\0")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def hash_password(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 100_000)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def check_password(pw: str, stored: str | None) -> bool:
    if not stored or "$" not in stored:
        return False
    salt = base64.b64decode(stored.split("$")[0])
    return hmac.compare_digest(hash_password(pw, salt), stored)


def create_app(st: State) -> FastAPI:
    app = FastAPI(title="supabase-lite")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "Location",
            "Upload-Offset",
            "Upload-Length",
            "Tus-Resumable",
            "Tus-Version",
            "Content-Range",
            "Accept-Ranges",
            "Content-Length",
        ],
    )

    # ------------------------------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------------------------------
    def user_json(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "aud": "authenticated",
            "role": "authenticated",
            "email": row["email"],
            "email_confirmed_at": row["created_at"].isoformat(),
            "app_metadata": {"provider": "email", "providers": ["email"]},
            "user_metadata": row["raw_user_meta_data"] or {},
            "identities": [],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["created_at"].isoformat(),
        }

    def session(row: dict[str, Any]) -> dict[str, Any]:
        now = int(time.time())
        claims = {
            "sub": str(row["id"]),
            "aud": "authenticated",
            "role": "authenticated",
            "email": row["email"],
            "iat": now,
            "exp": now + JWT_TTL,
            "iss": f"{st.public_url}/auth/v1",
            "session_id": str(uuid.uuid4()),
        }
        token = jwt.encode(claims, st.key, algorithm="ES256", headers={"kid": st.kid})
        refresh = secrets.token_urlsafe(24)
        st.refresh[refresh] = str(row["id"])
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": JWT_TTL,
            "expires_at": now + JWT_TTL,
            "refresh_token": refresh,
            "user": user_json(row),
        }

    def user_from_token(token: str) -> dict[str, Any]:
        try:
            return jwt.decode(token, st.key.public_key(), algorithms=["ES256"], audience="authenticated")
        except jwt.PyJWTError as e:
            raise HTTPException(401, {"code": "bad_jwt", "message": str(e)}) from e

    def bearer(request: Request) -> str | None:
        h = request.headers.get("authorization", "")
        return h[7:] if h.lower().startswith("bearer ") else None

    @app.get("/auth/v1/.well-known/jwks.json")
    def jwks() -> dict[str, Any]:
        nums = st.key.public_key().public_numbers()
        return {
            "keys": [
                {
                    "kty": "EC",
                    "crv": "P-256",
                    "x": b64url(nums.x),
                    "y": b64url(nums.y),
                    "kid": st.kid,
                    "alg": "ES256",
                    "use": "sig",
                }
            ]
        }

    @app.get("/auth/v1/settings")
    def settings() -> dict[str, Any]:
        return {
            "external": {"email": True, "google": False, "github": False},
            "disable_signup": False,
            "mailer_autoconfirm": True,
        }

    @app.post("/auth/v1/signup")
    async def signup(request: Request) -> JSONResponse:
        body = await request.json()
        email, password = str(body.get("email", "")).lower(), str(body.get("password", ""))
        if not email or len(password) < 6:
            return JSONResponse(
                {"code": 422, "error_code": "weak_password", "msg": "Password should be at least 6 characters."}, 422
            )
        with st.db() as conn:
            if conn.execute("select 1 from auth.users where email = %s", (email,)).fetchone():
                return JSONResponse(
                    {"code": 422, "error_code": "user_already_exists", "msg": "User already registered"}, 422
                )
            row = conn.execute(
                "insert into auth.users (id, email, encrypted_password, raw_user_meta_data)"
                " values (%s, %s, %s, %s) returning *",
                (str(uuid.uuid4()), email, hash_password(password), json.dumps(body.get("data") or {})),
            ).fetchone()
        assert row is not None
        return JSONResponse(session(row))

    @app.post("/auth/v1/token")
    async def token(request: Request, grant_type: str) -> JSONResponse:
        body = await request.json()
        with st.db() as conn:
            if grant_type == "password":
                row = conn.execute(
                    "select * from auth.users where email = %s", (str(body.get("email", "")).lower(),)
                ).fetchone()
                if not row or not check_password(str(body.get("password", "")), row["encrypted_password"]):
                    return JSONResponse(
                        {"code": 400, "error_code": "invalid_credentials", "msg": "Invalid login credentials"}, 400
                    )
                return JSONResponse(session(row))
            if grant_type == "refresh_token":
                uid = st.refresh.pop(str(body.get("refresh_token", "")), None)
                row = conn.execute("select * from auth.users where id = %s", (uid,)).fetchone() if uid else None
                if not row:
                    return JSONResponse(
                        {"code": 400, "error_code": "refresh_token_not_found", "msg": "Invalid Refresh Token"}, 400
                    )
                return JSONResponse(session(row))
        return JSONResponse({"code": 400, "msg": f"unsupported grant_type {grant_type}"}, 400)

    @app.get("/auth/v1/user")
    def get_user(request: Request) -> dict[str, Any]:
        claims = user_from_token(bearer(request) or "")
        with st.db() as conn:
            row = conn.execute("select * from auth.users where id = %s", (claims["sub"],)).fetchone()
        if not row:
            raise HTTPException(404, "user not found")
        return user_json(row)

    @app.post("/auth/v1/logout")
    def logout() -> Response:
        return Response(status_code=204)

    # ------------------------------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------------------------------
    def obj_path(bucket: str, path: str) -> Path:
        p = (st.root / bucket / path).resolve()
        if not str(p).startswith(str(st.root.resolve())):
            raise HTTPException(400, "bad path")
        return p

    def require_service(request: Request) -> None:
        key = request.headers.get("apikey") or bearer(request)
        if key != SERVICE_KEY:
            raise HTTPException(403, {"statusCode": "403", "error": "Unauthorized", "message": "service key required"})

    def signature(bucket: str, path: str, exp: int) -> str:
        msg = f"{bucket}/{path}:{exp}".encode()
        return base64.urlsafe_b64encode(hmac.new(st.sign_secret, msg, hashlib.sha256).digest()).decode().rstrip("=")

    def signed(bucket: str, path: str, expires_in: int) -> str:
        exp = int(time.time()) + int(expires_in)
        return f"/object/sign/{bucket}/{path}?token={exp}.{signature(bucket, path, exp)}"

    @app.post("/storage/v1/object/sign/{bucket}/{path:path}")
    async def sign_one(bucket: str, path: str, request: Request) -> dict[str, str]:
        require_service(request)
        body = await request.json()
        if not obj_path(bucket, path).is_file():
            raise HTTPException(404, {"statusCode": "404", "error": "not_found", "message": "Object not found"})
        return {"signedURL": signed(bucket, path, body.get("expiresIn", 3600))}

    @app.post("/storage/v1/object/sign/{bucket}")
    async def sign_many(bucket: str, request: Request) -> list[dict[str, Any]]:
        require_service(request)
        body = await request.json()
        out = []
        for p in body.get("paths", []):
            if obj_path(bucket, p).is_file():
                out.append({"path": p, "signedURL": signed(bucket, p, body.get("expiresIn", 3600)), "error": None})
            else:
                out.append(
                    {
                        "path": p,
                        "signedURL": None,
                        "error": "Either the object does not exist or you do not have access to it",
                    }
                )
        return out

    @app.get("/storage/v1/object/sign/{bucket}/{path:path}")
    def get_signed(bucket: str, path: str, token: str, download: str | None = None) -> FileResponse:
        try:
            exp_s, sig = token.split(".", 1)
            exp = int(exp_s)
        except ValueError as e:
            raise HTTPException(400, "bad token") from e
        if exp < time.time() or not hmac.compare_digest(sig, signature(bucket, path, exp)):
            raise HTTPException(
                400, {"statusCode": "400", "error": "InvalidJWT", "message": "signature verification failed"}
            )
        p = obj_path(bucket, path)
        if not p.is_file():
            raise HTTPException(404, "not found")
        media = (
            "video/mp4" if p.suffix == ".mp4" else "image/jpeg" if p.suffix == ".jpg" else "application/octet-stream"
        )
        return FileResponse(
            p,
            media_type=media,
            filename=download or None,
            content_disposition_type="attachment" if download else "inline",
        )

    @app.head("/storage/v1/object/{bucket}/{path:path}")
    def head_object(bucket: str, path: str, request: Request) -> Response:
        require_service(request)
        p = obj_path(bucket, path)
        if not p.is_file():
            return Response(status_code=404)
        return Response(headers={"content-length": str(p.stat().st_size)})

    @app.get("/storage/v1/object/{bucket}/{path:path}")
    def get_object(bucket: str, path: str, request: Request) -> FileResponse:
        require_service(request)
        p = obj_path(bucket, path)
        if not p.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(p)

    @app.post("/storage/v1/object/list/{bucket}")
    async def list_objects(bucket: str, request: Request) -> list[dict[str, Any]]:
        require_service(request)
        body = await request.json()
        d = obj_path(bucket, body.get("prefix", ""))
        if not d.is_dir():
            return []
        return [
            {"name": f.name, "id": str(uuid.uuid5(uuid.NAMESPACE_URL, str(f)))}
            for f in sorted(d.iterdir())
            if f.is_file()
        ]

    @app.post("/storage/v1/object/{bucket}/{path:path}")
    async def upload_object(bucket: str, path: str, request: Request) -> dict[str, str]:
        require_service(request)
        p = obj_path(bucket, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        return {"Key": f"{bucket}/{path}"}

    @app.delete("/storage/v1/object/{bucket}")
    async def delete_objects(bucket: str, request: Request) -> list[dict[str, str]]:
        require_service(request)
        body = await request.json()
        out = []
        for prefix in body.get("prefixes", []):
            p = obj_path(bucket, prefix)
            if p.is_file():
                p.unlink()
                out.append({"name": prefix})
        return out

    # TUS resumable uploads (user tokens; the raw bucket's "own folder" policy is enforced here).
    @app.options("/storage/v1/upload/resumable{rest:path}")
    def tus_options(rest: str) -> Response:
        return Response(status_code=204, headers=TUS_HEADERS)

    def tus_user(request: Request) -> str:
        token = bearer(request)
        if not token:
            raise HTTPException(401, "missing token")
        return str(user_from_token(token)["sub"])

    @app.post("/storage/v1/upload/resumable")
    async def tus_create(request: Request) -> Response:
        uid = tus_user(request)
        meta = {}
        for pair in (request.headers.get("upload-metadata") or "").split(","):
            if pair.strip():
                k, _, v = pair.strip().partition(" ")
                meta[k] = base64.b64decode(v).decode() if v else ""
        bucket, name = meta.get("bucketName", ""), meta.get("objectName", "")
        if bucket != "raw" or not name.startswith(f"{uid}/"):
            return Response("new row violates row-level security policy", status_code=403, headers=TUS_HEADERS)
        length = int(request.headers.get("upload-length", "0"))
        if length > 500 * 1024 * 1024:
            return Response("Payload too large", status_code=413, headers=TUS_HEADERS)
        upload_id = secrets.token_hex(12)
        part = st.root / ".tus" / upload_id
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(b"")
        st.uploads[upload_id] = {"bucket": bucket, "name": name, "length": length, "offset": 0, "uid": uid}
        body = await request.body()
        if body:
            _tus_append(upload_id, body)
        headers = {
            **TUS_HEADERS,
            "Location": f"{st.public_url}/storage/v1/upload/resumable/{upload_id}",
            "Upload-Offset": str(st.uploads[upload_id]["offset"]),
        }
        return Response(status_code=201, headers=headers)

    def _tus_append(upload_id: str, data: bytes) -> None:
        up = st.uploads[upload_id]
        with (st.root / ".tus" / upload_id).open("ab") as f:
            f.write(data)
        up["offset"] += len(data)
        if up["offset"] >= up["length"]:
            dest = obj_path(up["bucket"], up["name"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            (st.root / ".tus" / upload_id).replace(dest)

    @app.head("/storage/v1/upload/resumable/{upload_id}")
    def tus_head(upload_id: str, request: Request) -> Response:
        tus_user(request)
        up = st.uploads.get(upload_id)
        if not up:
            return Response(status_code=404, headers=TUS_HEADERS)
        return Response(
            headers={
                **TUS_HEADERS,
                "Upload-Offset": str(up["offset"]),
                "Upload-Length": str(up["length"]),
                "Cache-Control": "no-store",
            }
        )

    @app.patch("/storage/v1/upload/resumable/{upload_id}")
    async def tus_patch(upload_id: str, request: Request) -> Response:
        uid = tus_user(request)
        up = st.uploads.get(upload_id)
        if not up or up["uid"] != uid:
            return Response(status_code=404, headers=TUS_HEADERS)
        if int(request.headers.get("upload-offset", "-1")) != up["offset"]:
            return Response(status_code=409, headers=TUS_HEADERS)
        _tus_append(upload_id, await request.body())
        return Response(status_code=204, headers={**TUS_HEADERS, "Upload-Offset": str(up["offset"])})

    @app.delete("/storage/v1/upload/resumable/{upload_id}")
    def tus_delete(upload_id: str, request: Request) -> Response:
        tus_user(request)
        st.uploads.pop(upload_id, None)
        (st.root / ".tus" / upload_id).unlink(missing_ok=True)
        return Response(status_code=204, headers=TUS_HEADERS)

    # ------------------------------------------------------------------------------------------
    # Realtime (Phoenix v2 serializer: [join_ref, ref, topic, event, payload])
    # ------------------------------------------------------------------------------------------
    @app.websocket("/realtime/v1/websocket")
    async def realtime(ws: WebSocket) -> None:
        await ws.accept()
        sock = RealtimeSocket(ws, st)
        st.sockets.add(sock)
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                await sock.handle(*msg)
        except WebSocketDisconnect, RuntimeError:
            pass
        finally:
            st.sockets.discard(sock)

    @app.on_event("startup")
    async def _startup() -> None:
        st.loop = asyncio.get_running_loop()
        threading.Thread(target=listen_changes, args=(st,), daemon=True).start()

    return app


class RealtimeSocket:
    def __init__(self, ws: WebSocket, st: State):
        self.ws = ws
        self.st = st
        self.channels: dict[str, dict[str, Any]] = {}  # topic -> {join_ref, user, bindings}

    async def send(self, join_ref: Any, ref: Any, topic: str, event: str, payload: Any) -> None:
        await self.ws.send_text(json.dumps([join_ref, ref, topic, event, payload], default=str))

    async def handle(self, join_ref: Any, ref: Any, topic: str, event: str, payload: dict[str, Any]) -> None:
        if topic == "phoenix" and event == "heartbeat":
            await self.send(None, ref, "phoenix", "phx_reply", {"status": "ok", "response": {}})
            return
        if event == "phx_join":
            user = None
            token = payload.get("access_token")
            if token and token.count(".") == 2:
                try:
                    user = jwt.decode(token, self.st.key.public_key(), algorithms=["ES256"], audience="authenticated")[
                        "sub"
                    ]
                except jwt.PyJWTError:
                    user = None
            bindings = []
            for i, b in enumerate((payload.get("config") or {}).get("postgres_changes") or []):
                bindings.append({**b, "id": i + 1})
            self.channels[topic] = {"join_ref": join_ref, "user": user, "bindings": bindings}
            await self.send(
                join_ref, ref, topic, "phx_reply", {"status": "ok", "response": {"postgres_changes": bindings}}
            )
            return
        if event == "access_token":
            ch = self.channels.get(topic)
            if ch:
                with contextlib.suppress(Exception):
                    ch["user"] = jwt.decode(
                        payload["access_token"],
                        self.st.key.public_key(),
                        algorithms=["ES256"],
                        audience="authenticated",
                    )["sub"]
            return
        if event == "phx_leave":
            self.channels.pop(topic, None)
            await self.send(join_ref, ref, topic, "phx_reply", {"status": "ok", "response": {}})
            return
        await self.send(join_ref, ref, topic, "phx_reply", {"status": "ok", "response": {}})

    async def deliver(self, change: dict[str, Any]) -> None:
        row = change.get("record") or change.get("old_record") or {}
        for topic, ch in list(self.channels.items()):
            if not ch["user"] or str(row.get("user_id")) != ch["user"]:
                continue  # RLS: users only see their own rows
            ids = []
            for b in ch["bindings"]:
                if b.get("table") not in (change["table"], "*") or b.get("schema") not in (change["schema"], "*"):
                    continue
                if b.get("event", "*") not in ("*", change["type"]):
                    continue
                flt = b.get("filter")
                if flt:
                    col, _, rest = flt.partition("=")
                    op, _, val = rest.partition(".")
                    if op != "eq" or str(row.get(col)) != val:
                        continue
                ids.append(b["id"])
            if ids:
                data = {
                    "schema": change["schema"], "table": change["table"], "type": change["type"],
                    "commit_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "columns": [], "record": change.get("record"), "old_record": change.get("old_record"),
                    "errors": None,
                }  # fmt: skip
                with contextlib.suppress(Exception):
                    await self.send(None, None, topic, "postgres_changes", {"ids": ids, "data": data})


TRIGGER_SQL = """
alter table auth.users add column if not exists encrypted_password text;
create or replace function public._lite_notify() returns trigger language plpgsql as $$
begin
  perform pg_notify('lite_changes', json_build_object(
    'schema', TG_TABLE_SCHEMA, 'table', TG_TABLE_NAME, 'type', TG_OP,
    'record', case when TG_OP <> 'DELETE' then row_to_json(new) end,
    'old_record', case when TG_OP <> 'INSERT' then row_to_json(old) end)::text);
  return null;
end $$;
drop trigger if exists _lite_clips on public.clips;
create trigger _lite_clips after insert or update or delete on public.clips
  for each row execute function public._lite_notify();
"""


def listen_changes(st: State) -> None:
    while True:
        try:
            with psycopg.connect(st.db_url, autocommit=True) as conn:
                conn.execute("listen lite_changes")
                for n in conn.notifies():
                    change = json.loads(n.payload)
                    if st.loop is None:
                        continue
                    for sock in list(st.sockets):
                        asyncio.run_coroutine_threadsafe(sock.deliver(change), st.loop)
        except Exception:
            time.sleep(2)


def prepare_db(admin_url: str, name: str, fresh: bool) -> str:
    from tracer_core.testing.fixtures import apply_migrations

    base, _, _ = admin_url.rpartition("/")
    url = f"{base}/{name}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        exists = admin.execute("select 1 from pg_database where datname = %s", (name,)).fetchone()
        if exists and fresh:
            admin.execute(f'drop database "{name}" with (force)')  # type: ignore[arg-type]
            exists = None
        if not exists:
            admin.execute(f'create database "{name}"')  # type: ignore[arg-type]
            apply_migrations(url)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(TRIGGER_SQL)  # type: ignore[arg-type]
    return url


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="postgresql://postgres@127.0.0.1:54329/postgres", help="admin connection URL")
    ap.add_argument("--db-name", default="tracer_dev")
    ap.add_argument("--fresh", action="store_true", help="drop and recreate the database")
    ap.add_argument("--storage", default=".supabase-lite/storage")
    ap.add_argument("--port", type=int, default=54321)
    args = ap.parse_args()
    url = prepare_db(args.db, args.db_name, args.fresh)
    root = Path(args.storage)
    root.mkdir(parents=True, exist_ok=True)
    st = State(url, root, f"http://127.0.0.1:{args.port}")
    print(f"supabase-lite on :{args.port}  db={url}  anon={ANON_KEY}  service={SERVICE_KEY}", flush=True)
    uvicorn.run(create_app(st), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
