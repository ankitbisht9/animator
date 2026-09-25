# Progress

Phases follow Section 20 of the build spec. Each phase ends with all tests green, a commit, and an
update here.

| Phase | Status |
|---|---|
| 0 Foundations | done |
| 1 DSL and timeline core | not started |
| 2 Evaluation engine + SVG slice | not started |
| 3 Tier 1 components, layout, camera | not started |
| 4 Solvers, bindings, SceneState, worker protocol | not started |
| 5 Hand-authored Archimedes, **Gate A** | not started |

## Phase 0: Foundations

- Monorepo: pnpm workspaces (`apps/web`, `packages/*`), uv workspace (`apps/api`, `contracts/python`),
  root `Makefile` (`setup dev test lint codegen check-codegen export-demo eval spikes`), CI workflow,
  `.env.example` and a filled `.env` (secrets left blank).
- Engine worker skeleton: NDJSON JSON-RPC 2.0 over stdio, `version` handshake, `health.ping`,
  debug-only failure injection hooks. Bundled with esbuild to `packages/engine-worker/dist/worker.cjs`.
- Python engine client: `EngineWorker` (subprocess, per-call timeout, kill on timeout) and
  `EngineWorkerPool` (least-loaded dispatch, restart-and-retry, restart budget, health checks).
  Protocol major-version check against generated `animator_contracts`.
- Minimal FastAPI app with `/api/health` that reports the engine handshake.
- Spikes:
  - (a) export throughput + determinism: 34.7 fps with 4 browsers at 1080p; found and fixed
    paint-history non-determinism (ADR-006).
  - (b) MathJax SVG: deterministic across runs and processes, ~2 ms/expression warm (ADR-005).
  - (c) OpenAI strict Structured Outputs: runs against the real LLM-facing schema generated in
    Phase 1 (ADR-007).
- Tests: 6 TS (JSON-RPC dispatcher), 11 Python (handshake, crash, timeout, restart budget, protocol
  mismatch, missing bundle, API health).

### Environment notes

- `uv` 0.12 and GNU make 4.4 were installed for the current user (`~/.local/bin`, winget).
- Model IDs and prices in `.env` / `config/pricing.json` were read from OpenAI's docs on 2026-09-25.
