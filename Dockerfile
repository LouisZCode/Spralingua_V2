# syntax=docker/dockerfile:1
# Backend image — FastAPI + Pipecat. Build context is the repo root; the
# frontend/ tree is excluded via .dockerignore (it ships as its own image).

# ---- Builder: compile dependencies (incl. pyaudio) into a venv ----
FROM python:3.12-slim AS builder

# uv: fast, lockfile-pinned installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Build tools for any dependency that ships an sdist instead of a wheel.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install only dependencies (not the app itself) for a cacheable layer.
# --frozen pins to uv.lock so prod can't drift from the lockfile.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project --no-dev

# ---- Runtime: slim image with just the shared libs the deps need ----
FROM python:3.12-slim AS runtime

# ffmpeg: pydub MP3 export. libssl3/libasound2: Azure Speech SDK. libgomp1:
# onnxruntime (silero VAD). postgresql-client: pg_dump for the REL-002
# offsite backup cron (scripts/pg_dump_to_bucket.py) -- python:3.12-slim is
# Debian 13 "trixie" as of this image, whose postgresql-client metapackage
# already resolves to postgresql-client-17, so no PGDG apt repo is needed to
# match Railway's Postgres 16/17 (an older trixie/bookworm base pinned to
# postgresql-client-15 would need one; re-check this if the base image ever
# moves back to bookworm).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        libssl3 \
        libasound2 \
        libgomp1 \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Prebuilt venv from the builder, then the app source (frontend/, .env, logs/
# are excluded via .dockerignore).
COPY --from=builder /app/.venv /app/.venv
COPY . .

# WS + HTTP share one port. Railway injects $PORT; 8765 is the local default.
EXPOSE 8765

# Migrations run as a separate pre-deploy step (`alembic upgrade head`), never
# here, so concurrent replicas can't race the schema. `exec` makes uvicorn PID 1
# so it receives SIGTERM for graceful pipeline shutdown.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8765}"]
