# syntax=docker/dockerfile:1
# ---- Plutus backend (FastAPI + uv + Alembic) ----
FROM python:3.12-slim

# uv binary (dependency manager). Pin a version for reproducible builds if desired.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install dependencies first so this layer is cached until the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application source.
COPY . .

# Run as a non-root user; ensure the import-upload cache dir is writable.
RUN useradd --create-home --uid 1001 appuser \
    && mkdir -p /app/.import_cache \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# /api/health does not touch the DB, so it stays healthy during startup/migrations.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

# Apply migrations, then start the API. (Alembic uses DATABASE_URL_SYNC; the app uses DATABASE_URL.)
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
