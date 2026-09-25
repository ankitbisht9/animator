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
- ffmpeg on `PATH` (for export)

## Commands

| Command | What it does |
|---|---|
| `make setup` | Install JS + Python deps, build the engine worker, install Chromium for Playwright |
| `make dev` | Web app on http://localhost:5173 and API on http://127.0.0.1:8000 |
| `make test` | All offline tests (no network, no API keys) |
| `make lint` | Biome + tsc, Ruff + mypy |
| `make codegen` | Regenerate `contracts/` and generated docs |
| `make check-codegen` | Fail if regenerated output differs from what is committed |
| `make export-demo` | Render the hand-authored Archimedes lesson to MP4 |
| `make spikes` | Re-run the Phase 0 spikes |

## Configuration

Copy `.env.example` to `.env` and fill in `OPENAI_API_KEY`. Every model ID, reasoning effort and budget is
an environment variable; prices live in `config/pricing.json`. There is no database yet (ADR-003).
