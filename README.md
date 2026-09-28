# Animator

Turns a request like *"Explain Archimedes' principle to a high-school student in 5 minutes"* into a clean,
flat, vector-style **2D animated explanation**. An LLM directs (plans the lesson and describes scenes as
structured data); a deterministic TypeScript engine renders (layout, physics, interpolation, SVG). No
image or video generation models are involved.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/DECISIONS.md](docs/DECISIONS.md) and
[PROGRESS.md](PROGRESS.md).

## Requirements

- Node 24 and pnpm 10
- [uv](https://docs.astral.sh/uv/) (installs Python 3.14 automatically)
- GNU make (Windows: `winget install ezwinports.make`)
- ffmpeg on `PATH` (for export and its tests)
- Docker (optional: only for the pinned export image)

## Commands

| Command | What it does |
|---|---|
| `make setup` | Install JS + Python deps, build the engine worker, install Chromium for Playwright |
| `make dev` | Web app on http://localhost:5173 and API on http://127.0.0.1:8000 |
| `make test` | All offline tests (no network, no API keys) |
| `make lint` | Biome + tsc, Ruff + mypy |
| `make codegen` | Regenerate `contracts/` and generated docs |
| `make check-codegen` | Fail if regenerated output differs from what is committed |
| `make export-demo` | Render the hand-authored Archimedes lesson to MP4 (`var/exports/archimedes/`) |
| `make export LESSON=slug` | Render any fixture lesson to MP4 |
| `make export-demo-docker` / `make test-docker` | The same inside the pinned Docker image (written, not yet verified: ADR-026) |
| `make spikes` | Re-run the Phase 0 spikes |

## Running it

`make dev` starts both halves together, with prefixed output (`[web]`, `[api]`); Ctrl+C stops both.

| Part | What it is | Default URL |
|---|---|---|
| Frontend | Vite + React: lesson player, DSL Playground, render page | http://localhost:5173 |
| Backend | FastAPI, which starts the engine worker pool (Node subprocesses) | http://127.0.0.1:8000/api/health |

The frontend proxies `/api` to the backend, so the browser only talks to port 5173. The lesson player
compiles scenes in the browser and does not need the backend; generation (Phase 7+) will.

Ports already taken by something else? Pick others:

```
# PowerShell
$env:API_PORT=8001; $env:WEB_DEV_PORT=5174; make dev
# bash
API_PORT=8001 WEB_DEV_PORT=5174 make dev
```

Each half can also run on its own:

```
pnpm --filter @animator/web dev                  # frontend only
make build-worker                                # backend needs the bundled engine worker
uv run uvicorn animator.api.app:app --reload --reload-dir apps/api/src --port 8000 --loop animator.api.loop:loop_factory
```

`--loop animator.api.loop:loop_factory` matters on Windows: with `--reload`, uvicorn otherwise picks an
event loop that cannot start the engine worker subprocess, and startup fails with `NotImplementedError`.

Then open http://localhost:5173/lesson/archimedes (12 scenes, about 4.5 minutes), or the DSL Playground
at http://localhost:5173/playground. To render still frames to PNG without the dev server:

```
make build-web
uv run python scripts/render_keyframes.py archimedes --scene 8 --times 16 24
```

`--scene` is 0-based; images go to `var/keyframes/archimedes/`.

## Exporting video

```
make export-demo                     # Archimedes, 1080p30
make export LESSON=gallery           # any fixture lesson, on this machine
uv run python -m animator.export --lesson engine-tour --width 1280 --height 720 --workers 4
uv run python -m animator.export --lesson archimedes --scenes 1-3      # some segments now...
uv run python -m animator.export --lesson archimedes                   # ...the rest later (cached)
uv run python -m animator.export --bundle var/exports/archimedes/archimedes.animation.json --name copy
```

Output goes to `var/exports/<name>/`: the MP4 (plus WebM with `--formats mp4,webm`), per-scene segments,
an `.animation.json` bundle that can be re-rendered, and an `.export.json` report with versions, timings
and frame hashes. `--audio track.wav` muxes an audio track, padded or trimmed to the video. The same lesson always gives
identical frames and a byte-identical MP4 on the same machine. The pinned Docker image
(`docker/export.Dockerfile`), which will make this hold across machines, is not verified yet (ADR-026).

## Configuration

Copy `.env.example` to `.env` and fill in `OPENAI_API_KEY`. Every model ID, reasoning effort and budget is
an environment variable; prices live in `config/pricing.json`. There is no database yet (ADR-003).
