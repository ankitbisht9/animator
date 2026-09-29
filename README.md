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

## Creating a lesson

1. Put a working `OPENAI_API_KEY` in `.env` (see below), then run `make dev`.
2. Open the web app, type a topic ("Explain Archimedes' principle"), choose the audience and length, and click
   **Create lesson**.
3. The lesson page shows live progress: planning, storyboarding each chapter, then writing, checking and fixing
   each scene. Scenes start playing as soon as the first one is ready; if playback catches up with generation, a
   "Preparing the next scene" overlay waits and playback resumes by itself.
4. **Export MP4** renders the finished lesson to video. **Debug** (`/debug/<id>`) shows the whole trace: the plan,
   each scene's brief, every attempt with the engine's issues, the scene DSL (open it in the Playground), and every
   AI call with tokens, cost and latency.

Generations are saved as JSON under `var/generations/` (no database). A lesson in progress when the server stops
resumes when it starts again. **Resume** continues a paused one, for example after an outage or after you fix the
key.

## Configuration

Copy `.env.example` to `.env`. The only value you must fill in is `OPENAI_API_KEY`.

- `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`: leave empty. A project key (`sk-proj-...`) already identifies its
  project. Set the organization only if you use an older user key that belongs to several organizations (its id
  looks like `org-...`, under Settings > Organization on platform.openai.com).
- Models per stage: `ANIMATOR_MODEL_*` and `ANIMATOR_EFFORT_*`. If a model is not available to your key, the next
  one in `ANIMATOR_MODEL_FALLBACKS` (default `gpt-5,gpt-4.1,gpt-4o`) is used automatically. Which models take a
  reasoning effort is in `config/models.json`; prices (for cost reports and the budget) are in
  `config/pricing.json`.
- Speed: generation keeps up with playback. A 30-second lesson is ready in about 21-33 s (median about 26 s),
  and a 1-minute lesson in about 30 s. The first scene plays after about 15-25 s (ADR-033). Scenes are drawn in parallel, each on a clean stage
  (`ANIMATOR_SCENE_MODE=parallel`). Short lessons are planned in one streamed call. Each scene is drafted twice at
  once and the first valid draft wins (`ANIMATOR_SCENE_DRAFTS`). Corrections send only what changes
  (`ANIMATOR_PATCH_CORRECTIONS`). OpenAI's priority tier is used (`OPENAI_SERVICE_TIER=priority`).
- Cost: `gpt-6-luna` at low effort for every stage, about $0.02 per 30 seconds of lesson. For about half that,
  and roughly 2x the generation time, set `OPENAI_SERVICE_TIER=` (empty) and `ANIMATOR_SCENE_DRAFTS=1`. For the
  best quality, at many times the cost and time, set the `ANIMATOR_MODEL_*` stages to `gpt-6-sol` with effort
  `high`.
  `LLM_BUDGET_USD_PER_GENERATION` is a hard stop per lesson, and the "Save cost" option on the form only
  generates a little ahead of what you watch.
- `ANIMATOR_LLM_PROVIDER`: `openai` (live), `record` (live, and also saves every call under
  `ANIMATOR_LLM_FIXTURES_DIR` for offline replay), `replay` (offline from those recordings).
- Optional quality passes: `ANIMATOR_ENABLE_LLM_CRITIC`, `ANIMATOR_ENABLE_VISUAL_CRITIC` (reviews rendered
  keyframes with a vision model; slower and more expensive).

No database yet (ADR-003); every setting is an environment variable, nothing is hardcoded.

### Working on the UI without an API key

`uv run python scripts/dev_fake_llm.py --port 8000 --delay 4` runs the API with a scripted fake LLM. Every topic
gets the hand-authored Archimedes lesson, with delays that imitate real generation. Start the web app as usual.
It exercises the whole UI (progress, progressive playback, debug page, export), but says nothing about real lesson
quality.

## Evaluation

`make eval` (or `uv run python -m animator.eval --only archimedes,photosynthesis`) generates the topics in
`config/eval_topics.json` with the live models. It writes `var/eval/<run>/report.md` with first-pass validity,
fixes per scene, the degraded rate, time to first play and cost per minute of video. For a model bake-off, run it
once per configuration with `--set ANIMATOR_MODEL_ANIMATION_PLANNER=...` and compare the reports.
