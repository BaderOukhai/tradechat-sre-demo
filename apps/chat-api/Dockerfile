# --- Stage 1: build. Has uv and the build cache; thrown away at the end. ---
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /bin/uv
WORKDIR /app
# Dependencies first: this layer is cached until pyproject.toml or uv.lock changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# --- Stage 2: runtime. Only Python, the virtualenv and our code: no uv, no build tools. ---
# Same base image as the build stage, so the virtualenv's Python paths stay valid.
FROM python:3.12-slim
# Fixed numeric UID: Kubernetes can then verify "runAsNonRoot" without guessing.
RUN useradd --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY main.py .
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
