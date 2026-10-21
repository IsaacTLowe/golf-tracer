# syntax=docker/dockerfile:1.7
# Build from the repo root: docker build -f services/worker/Dockerfile .
FROM python:3.14.6-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/tracer-spec/python/pyproject.toml packages/tracer-spec/python/
COPY python/tracer_core/pyproject.toml python/tracer_core/
COPY services/api/pyproject.toml services/api/
COPY services/worker/pyproject.toml services/worker/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package tracer-worker
COPY packages/tracer-spec/schema packages/tracer-spec/schema
COPY packages/tracer-spec/python packages/tracer-spec/python
COPY python/tracer_core python/tracer_core
COPY services/api services/api
COPY services/worker services/worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package tracer-worker

FROM python:3.14.6-slim-trixie
# ffmpeg/ffprobe for probing, normalizing and encoding. No PyTorch in the runtime image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 app && mkdir -p /work && chown app:app /work
COPY --from=build --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 WORK_DIR=/work
USER app
CMD ["tracer-worker"]
