# Same pattern as apps/chat-api/Dockerfile (see comments there), plus a writable archive dir.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
# /data is the only directory the app may write to (the archive).
RUN useradd --uid 10001 --no-create-home app \
    && mkdir /data && chown app /data
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY main.py .
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    ARCHIVE_DIR=/data
USER 10001
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
