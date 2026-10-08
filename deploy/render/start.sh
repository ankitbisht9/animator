#!/bin/sh
# Starts the platform on Render (docs/DEPLOY_RENDER.md). Render gives the service's own address in
# RENDER_EXTERNAL_URL: it becomes the platform's public address (links in mails, allowed origins)
# unless PLATFORM_PUBLIC_BASE_URL is set (a custom domain).
set -eu
: "${PLATFORM_PUBLIC_BASE_URL:=${RENDER_EXTERNAL_URL:-http://localhost:${PORT:-10000}}}"
: "${CORS_ORIGINS:=$PLATFORM_PUBLIC_BASE_URL}"
export PLATFORM_PUBLIC_BASE_URL CORS_ORIGINS

# One process: rate limits and caches live in memory (docs/PLATFORM.md). Render's proxy sets the
# forwarded headers, so the gateway sees real client addresses. (Through the environment, not as an
# argument: some launchers expand a bare * into file names.)
export FORWARDED_ALLOW_IPS="*"
exec uvicorn animator.api.app:app \
  --host 0.0.0.0 \
  --port "${PORT:-10000}" \
  --loop animator.api.loop:loop_factory \
  --proxy-headers \
  --timeout-keep-alive 75
