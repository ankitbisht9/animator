# syntax=docker/dockerfile:1.7
#
# The platform on Render (docs/DEPLOY_RENDER.md, ADR-054): one container that serves the API, its engine
# workers (Node) and the built web app from one origin. Built from docker/export.Dockerfile's pinned
# Node, Python and uv, without a browser: the visual critic and video export are off in this image.

ARG NODE_IMAGE=node:24.11.1-trixie-slim@sha256:4623e8ac1eb5f35b0a91b85424283c1b97a416a77375dfa6a2e9bfb0b2c351c9
ARG PYTHON_IMAGE=python:3.14.7-slim-trixie@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424

FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------- web app and engine worker (Node)
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
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# ffmpeg shapes and measures the narration audio.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/
# The engine workers run on the same Node as the build stage.
COPY --from=web /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY apps/api/pyproject.toml apps/api/
COPY apps/api/src apps/api/src
COPY contracts/python contracts/python
# Editable install (uv's default for workspace members): the API finds the repository's folders
# (config, the web build, the engine worker) next to its own source, as on a development machine.
RUN uv sync --frozen --no-dev --all-packages

COPY --from=web /src/apps/web/dist apps/web/dist
COPY --from=web /src/packages/engine-worker/dist/worker.cjs packages/engine-worker/dist/worker.cjs
COPY contracts contracts
COPY config config
COPY deploy/render/start.sh deploy/render/start.sh

# Not root. Lessons, audio and the platform's database are written under /app/var.
RUN useradd --create-home --uid 10001 animator \
 && mkdir -p /app/var \
 && chown -R animator:animator /app/var
USER animator

ENV PATH=/app/.venv/bin:$PATH \
    UV_OFFLINE=1 \
    UV_NO_SYNC=1
EXPOSE 10000
CMD ["sh", "deploy/render/start.sh"]
