# syntax=docker/dockerfile:1

# Stage 1: resolve the locked environment for the API package with uv.
FROM python:3.14.10-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# The workspace root manifest and lock file must match the repo, and uv only
# needs the members it is asked to install.
COPY pyproject.toml uv.lock ./
COPY packages/core ./packages/core
COPY apps/api ./apps/api

RUN uv sync --frozen --no-dev --package lmi-api

# Stage 2: runtime image. The same /app paths as the builder are required,
# because uv installs workspace members as editable installs.
FROM python:3.14.10-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/packages/core /app/packages/core
COPY --from=builder /app/apps/api /app/apps/api

RUN useradd --create-home --uid 1000 lmi

USER lmi

EXPOSE 8000

CMD ["uvicorn", "lmi_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
