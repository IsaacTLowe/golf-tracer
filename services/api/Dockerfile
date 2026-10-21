# syntax=docker/dockerfile:1.7
# Build from the repo root: docker build -f services/api/Dockerfile .
FROM python:3.14.6-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Workspace manifests first for layer caching.
COPY pyproject.toml uv.lock ./
COPY packages/tracer-spec/python/pyproject.toml packages/tracer-spec/python/
COPY python/tracer_core/pyproject.toml python/tracer_core/
COPY services/api/pyproject.toml services/api/
COPY services/worker/pyproject.toml services/worker/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package tracer-api
COPY packages/tracer-spec/schema packages/tracer-spec/schema
COPY packages/tracer-spec/python packages/tracer-spec/python
COPY python/tracer_core python/tracer_core
COPY services/api services/api
COPY services/worker services/worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package tracer-api

FROM python:3.14.6-slim-trixie
RUN useradd --create-home --uid 10001 app
COPY --from=build --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status == 200 else 1)"
CMD ["uvicorn", "--factory", "tracer_api.main:app_factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
