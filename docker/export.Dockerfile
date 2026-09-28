# syntax=docker/dockerfile:1.7
#
# Pinned export environment (spec Section 15, ADR-026): Python + Node + Chromium + ffmpeg + fonts.
# Every exported frame is rendered here by the same web build, the same Chromium and the same
# ffmpeg, so the same stored DSL gives identical frame hashes.
#
#   make docker-build       build this image
#   make export-demo        export the Archimedes lesson to var/exports/archimedes/
#   make test-docker        run the offline test suite inside the image (no network)
#
# Base images are pinned by digest, tools by exact version:
#   Node 24.11.1, pnpm 10.34.5 (lockfile), Python 3.14.7, uv 0.12.19 (uv.lock),
#   Playwright 1.63.0 (its Chromium build), ffmpeg 7.1.5 (Debian 13), DejaVu fonts 2.37.

ARG NODE_IMAGE=node:24.11.1-trixie-slim@sha256:4623e8ac1eb5f35b0a91b85424283c1b97a416a77375dfa6a2e9bfb0b2c351c9
ARG PYTHON_IMAGE=python:3.14.7-slim-trixie@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424

FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------- web build (Node)
FROM ${NODE_IMAGE} AS web
ENV CI=1 PNPM_HOME=/pnpm
RUN corepack enable && corepack prepare pnpm@10.34.5 --activate
WORKDIR /src
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
COPY apps/web/package.json apps/web/
COPY packages/engine/package.json packages/engine/
COPY packages/components/package.json packages/components/
COPY packages/engine-worker/package.json packages/engine-worker/
RUN pnpm install --frozen-lockfile
COPY tsconfig.base.json ./
COPY packages packages
COPY apps/web apps/web
COPY fixtures fixtures
RUN pnpm run build:worker && pnpm run build:web

# ---------------------------------------------------------------- runtime (Python)
FROM ${PYTHON_IMAGE} AS runtime
ARG FFMPEG_VERSION=7:7.1.5-0+deb13u1
ARG FONTS_VERSION=2.37-8
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update \
 && apt-get install -y --no-install-recommends "ffmpeg=${FFMPEG_VERSION}" "fonts-dejavu-core=${FONTS_VERSION}" \
 && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/
# The engine worker (Python tests and API) needs the same Node as the build stage.
COPY --from=web /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY apps/api/pyproject.toml apps/api/
COPY apps/api/src apps/api/src
COPY contracts/python contracts/python
RUN uv sync --frozen --all-packages
# Chromium is pinned by the Playwright version in uv.lock. Headless launches use Chromium's
# headless shell, so only that is installed; --with-deps adds its system libraries.
RUN .venv/bin/playwright install --with-deps --only-shell chromium && rm -rf /var/lib/apt/lists/*

COPY --from=web /src/apps/web/dist apps/web/dist
COPY --from=web /src/packages/engine-worker/dist packages/engine-worker/dist
COPY contracts contracts
COPY config config
COPY fixtures fixtures
COPY apps/api/tests apps/api/tests

# Runtime is offline: nothing is installed or fetched after this point.
ENV PATH=/app/.venv/bin:$PATH \
    UV_OFFLINE=1 \
    UV_NO_SYNC=1 \
    ANIMATOR_PINNED_ENV=1
CMD ["python", "-m", "animator.export.demo"]
