# CLAUDE.md — Golf Tracer

Project guidance for this repo. It applies on every machine (macOS and Windows). Read `README.md` for the architecture and `docs/DECISIONS.md` for choices already made.

## Coding principles

- **Consistent:** match the surrounding code's naming, structure, idioms, comment density and formatting. One way of doing a thing across the codebase.
- **Repeatable:** builds, tests, evals and deployments give the same result on any machine. Pin versions, use lockfiles (`uv.lock`, `pnpm-lock.yaml`), keep scripts cross-platform, and don't depend on local state that isn't documented.
- **KISS:** the simplest solution that fully solves the problem. No speculative abstractions, config or layers.
- **DRY:** one source of truth for each piece of logic or knowledge. Reuse existing utilities and patterns (for example `tracer_spec` for tracer math, `tracer_core.model` for building tracers) rather than duplicating them.
- **Standard design principles:** SOLID, YAGNI, separation of concerns, small single-purpose functions, clear names, fail fast at boundaries, and tests for behaviour that matters (Clean Code).
- **Security:** least privilege, validate input at trust boundaries, keep secrets out of git and out of clients (the service-role key never leaves the API and worker), keep RLS on every user table, and pin dependencies.
- **Deployability:** everything runs from the Docker images and env vars in `.env.example`; no machine-specific paths or manual steps. Migrations go in `supabase/migrations`.
- **Scalability:** stateless API and workers that scale by adding replicas, work goes through the job queue, video bytes never pass through the API, and memory use is bounded per clip.

## Setup

Prerequisites: Docker, Node ≥ 24 with pnpm 12.14.0 (`npm i -g pnpm@12.14.0`), uv, ffmpeg on `PATH`.

- **macOS:** `brew install uv ffmpeg colima docker docker-compose docker-buildx` (the Dockerfiles need BuildKit, which Docker Desktop bundles but Homebrew's CLI doesn't), then `colima start --cpu 4 --memory 8` before any Docker or Supabase command; the default 2 CPU / 4 GB VM is too small for the Supabase stack.
- **Windows:** Docker Desktop, `winget install astral-sh.uv Gyan.FFmpeg`.

Then `pnpm install && uv sync`. `.gitattributes` forces LF line endings so both machines produce identical diffs.

## Checks before calling work done

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy packages/tracer-spec/python python/tracer_core services/api services/worker
uv run pytest
pnpm -r lint && pnpm -r typecheck && pnpm -r test
uv run python -m tracer_core.eval --baseline datasets/golden/baseline_synthetic.json   # any tracing change
```

Tracing changes are judged by the eval numbers, not by eyeballing a few clips. When an improvement lands, re-record the baseline (`--record`) and note it in `docs/DECISIONS.md`.
