# Progress

Phases follow Section 20 of the build spec. Each phase ends with all tests green, a commit, and an
update here.

| Phase | Status |
|---|---|
| 0 Foundations | done |
| 1 DSL and timeline core | done |
| 2 Evaluation engine + SVG slice | done |
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
  - (c) OpenAI strict Structured Outputs: the generated LLM-facing schema passes every documented
    strict-mode rule (ADR-007). The live probe is written and skips until an API key is set.
- Tests: 6 TS (JSON-RPC dispatcher), 11 Python (handshake, crash, timeout, restart budget, protocol
  mismatch, missing bundle, API health).

### Environment notes

- `uv` 0.12 and GNU make 4.4 were installed for the current user (`~/.local/bin`, winget).
- Model IDs and prices in `.env` / `config/pricing.json` were read from OpenAI's docs on 2026-09-25.

## Phase 1: DSL and timeline core

- Canonical Scene DSL in Zod (`packages/engine/src/dsl`): scene, objects (one variant per registered
  component), 25 action types, 5 solver declarations, cues, beats, values, checkpoints. `dsl_version` 1.0.
- Generated contracts: `contracts/dsl.schema.json` (canonical) and `contracts/dsl.llm.schema.json`
  (strict-mode projection). `lift()` maps LLM output to canonical form (idempotent).
- Generated docs: `docs/DSL.md`, `docs/COMPONENTS.md`.
- Time model: relative timing (`at` seconds/cue/beat, `after`, `with`, `delay`), cycle detection, beat
  distribution, scene-overrun detection.
- Action compiler to per-object channels and segments; conflict detection; groups; morph point
  correspondence; camera focus/follow (baked); transitions.
- Interpolation (numbers, OKLab colors, keyframes), 18 easings + cubic-bezier, analytic spring.
- Core evaluator (`evaluate(compiled, t)`), expression language, number formatting, strict path
  parser, deterministic text layout from bundled Lexend + TeX-font symbol tables, themes.
- Semantic validation with machine-readable issues and "did you mean" suggestions; invalid-DSL corpus
  (`fixtures/invalid`, 23 cases).
- Tests: 71 engine tests incl. fast-check properties (evaluate purity, tween continuity/endpoints, easing
  monotonicity, PRNG purity) and compile determinism.

## Phase 2: Evaluation engine and SVG rendering slice

- `evaluate(compiled, t) -> FrameState` with visual and layout matrices, entrance/exit and emphasis
  effects, group bounds, captions, transitions, camera.
- `@animator/components`: `<SceneView>` (FrameState -> SVG), glyph-path text, backgrounds, caption bar
  with word-by-word reading highlight, emphasis overlays (glow, box, circle, underline, pulse, flash),
  soft-shadow filter, Tier 0 renderers.
- Web app (`apps/web`): lesson player (pure-time playback clock, scrubber with scene segments, speed,
  captions toggle, fullscreen, keyboard shortcuts, scene list with status, clickable transcript,
  hover-to-inspect objects while paused, checkpoint questions), DSL Playground (CodeMirror, live
  validation issues, play/scrub), headless `/render` page (`__loadLesson`, `__renderFrame`,
  `__renderAt`; fresh subtree per frame).
- Scene continuity: `sceneEndState` (engine-computed), `WorldRegistry`, carry-over into the next scene,
  continuity warnings (abrupt disappearance, identity drift), `summarizeState` for the LLM.
- Python render driver (`animator.export`): static site with SPA fallback, Chromium session;
  `scripts/render_keyframes.py` renders scene keyframes to PNG.
- Tests: SVG golden frames (10, platform-independent), renderer coverage, markup safety, PNG golden frames
  (Windows) with tolerance, pixel determinism in forward/reverse order, lesson building.
