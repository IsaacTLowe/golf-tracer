# Golf Tracer

Upload down-the-line swing clips and get them back with a broadcast-style ball tracer. Review each tracer, fix any that look wrong, then export an MP4 with the tracer burned in.

The build spec is [`GOLF_TRACER_SPEC.md`](GOLF_TRACER_SPEC.md). Choices made where it was silent are in [`docs/DECISIONS.md`](docs/DECISIONS.md), and the RLS review is in [`docs/RLS_REVIEW.md`](docs/RLS_REVIEW.md).

| Part | Where | Tech |
| --- | --- | --- |
| Web app (UI only) | `apps/web` | Next.js 16 (App Router), React 19, Tailwind 4 |
| Mobile spike | `apps/mobile` | Expo SDK 58 |
| REST API | `services/api` | FastAPI, `/v1`, Supabase JWT (JWKS) |
| Worker | `services/worker` | ffmpeg, OpenCV; `process`, `retrace`, `render` jobs |
| Shared Python | `python/tracer_core` | pipeline, job queue, storage, DB, eval, synthetic clips |
| Tracer spec | `packages/tracer-spec` | JSON Schema, TS and Python implementations, test vectors, parity check |
| API client | `packages/api-client` | generated from the OpenAPI schema (`openapi-typescript` + `openapi-fetch`) |
| Database | `supabase/` | config, migrations, buckets, RLS |

## Run it locally

You need Docker, Node 24 with pnpm 12 (`npm i -g pnpm@12.14.0`), and [uv](https://docs.astral.sh/uv/).

```bash
pnpm install
uv sync

# 1. Supabase (Postgres, Auth, Storage, Realtime) via the CLI. This applies supabase/migrations.
pnpm supabase:start          # also creates an ES256 signing key on first run
pnpm supabase:env            # writes the local keys into .env

# 2. Web, API and worker
docker compose up --build    # web http://localhost:3000, API http://localhost:8000/docs
```

Then open http://localhost:3000, create an account (local email confirmation is off), and drop in some clips. Locally sent emails, such as magic links, show up in Mailpit at http://127.0.0.1:54324. Scale workers with `docker compose up --scale worker=3`.

Useful settings (see `.env.example`):

- `TRACE_DEBUG=1` writes a debug video per clip (candidates, track, fitted curve) to `media/{user}/{clip}/debug.mp4`. A link to it appears on the review screen.
- `WATERMARK=1` adds a watermark to exports.
- To enable OAuth (Google, GitHub), set the provider credentials in `.env` and `enabled = true` in `supabase/config.toml`.

### Without Docker (development only)

`tools/supabase-lite` stands in for the slice of Supabase the app uses, on top of a plain Postgres:

```bash
uv run python tools/supabase-lite/server.py --db postgresql://postgres@127.0.0.1:5432/postgres --fresh
SUPABASE_URL=http://127.0.0.1:54321 SUPABASE_SERVICE_ROLE_KEY=service-role-dev-key \
  DATABASE_URL=postgresql://postgres@127.0.0.1:5432/tracer_dev uv run uvicorn --factory tracer_api.main:app_factory --port 8000
# same env: uv run tracer-worker   (needs ffmpeg on PATH)
cd apps/web && pnpm build && SUPABASE_URL=http://127.0.0.1:54321 SUPABASE_ANON_KEY=anon-dev-key \
  NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 pnpm start
```

## Tests

```bash
uv run ruff check . && uv run mypy packages/tracer-spec/python python/tracer_core services/api services/worker
uv run pytest                      # API, auth, RLS, job queue, pipeline, worker (needs Postgres: TEST_DATABASE_URL)
pnpm -r lint && pnpm -r typecheck && pnpm -r test          # TS packages + web
pnpm --filter @golf-tracer/web exec playwright test --project=parity   # canvas vs server render
E2E_BASE_URL=http://localhost:3000 pnpm --filter @golf-tracer/web exec playwright test --project=e2e --project=e2e-mobile
uv run python -m tracer_core.eval --baseline datasets/golden/baseline_synthetic.json   # tracer quality gate
uv run python tools/loadtest/batch_load.py --clip some.mp4 --count 50 --anon-key "$SUPABASE_ANON_KEY"
```

Python DB tests create and drop a temporary database on `TEST_DATABASE_URL` (default `postgresql://postgres:postgres@127.0.0.1:54329/postgres`). They apply a small Supabase stub plus the real migrations. CI (`.github/workflows/ci.yml`) runs everything, including the full stack via the Supabase CLI and `docker compose`.

After changing API models, regenerate the client (CI checks for drift):

```bash
uv run python services/api/scripts/export_openapi.py packages/api-client/openapi.json && pnpm gen:api-client
```

## How it works

1. **Upload:** the client calls `POST /v1/clips`, uploads straight to Supabase Storage over resumable TUS with its own token, then calls `POST /v1/clips/{id}/complete`. Video bytes never pass through the API.
2. **Process** (worker):
   - Probe and validate the file, then normalize it: H.264, constant frame rate, rotation applied, long edge ≤ 1920.
   - Find the ball at address, then detect impact from the audio strike and the ball leaving its spot.
   - Track the early flight, with camera-motion compensation for handheld clips.
   - Fit the trajectory: a 3D ball-flight model through an estimated camera, or an image-space fit when that fails.
   - Score confidence and route the clip to `review` or `needs_input`.
3. **Review** (web): the tracer is drawn live on a canvas over the video using the same math as the server. Approve it, edit it (handles, timeline markers, style, undo, versions), or place three points in the manual wizard.
4. **Export:** a `render` job burns the current tracer version into the MP4. Download it through a signed URL.

## Status vs milestones

| Milestone | State |
| --- | --- |
| 1 Scaffold | Done |
| 2 Auth, upload, library | Done |
| 3 Manual tracer, review, export | Done; parity test passes |
| 4 Auto tracing v1 | Done on synthetic data; **real golden clips still to be recorded** (see `datasets/golden/README.md`) |
| 5 Review speed and polish | Done |
| 6 Tracking v2 | Camera-motion compensation and 3D fit done; no learned detector (not justified by eval yet) |
| 7 Hardening and mobile prep | Rate limits, retention, cleanup, structured logs, load test script, RLS review and Expo spike done; the spike is bundled for iOS and Android but **not yet run on devices** |
