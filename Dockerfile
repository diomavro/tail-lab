# =============================================================================
# tail-lab — single-container deploy (Fly.io). FastAPI serves the built React
# SPA directly (see api/main.py's StaticFiles mount) — one image, one
# machine, no nginx, no reverse proxy.
#
# Two-stage build:
#   stage 1 (frontend-builder): node:24-alpine, npm ci + vite build -> dist/
#   stage 2 (runtime): python:3.12-slim + backend code + dist/
#
# Deploying this is a HUMAN_TODO (no Fly account wired up yet, per the
# walking-skeleton brief): `fly launch` / `fly deploy` from repo root once a
# Fly app + volume exist.
#
# Build: docker build -t tail-lab .
# Run:   docker run --rm -p 8080:8080 -v tail_lab_data:/data tail-lab
# =============================================================================

# ---- Stage 1: frontend build ----
FROM node:24-alpine AS frontend-builder

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# ---- Stage 2: runtime ----
FROM python:3.12-slim AS runtime

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

COPY --from=frontend-builder /app/dist ./frontend/dist

RUN groupadd --system --gid 1001 taillab \
    && useradd --system --uid 1001 --gid taillab taillab \
    && mkdir -p /data \
    && chown -R taillab:taillab /app /data

USER taillab

EXPOSE 8080

# The package is pip-installed into site-packages, so main.py's relative
# fallback can't find the SPA — point it at where dist was copied.
ENV TAIL_LAB_STATIC_DIR=/app/frontend/dist

CMD ["uvicorn", "tail_lab.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
