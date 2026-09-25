# Progress

Phases follow Section 20 of the build spec. Each phase ends with all tests green, a commit, and an
update here.

| Phase | Status |
|---|---|
| 0 Foundations | done |
| 1 DSL and timeline core | done |
| 2 Evaluation engine + SVG slice | done |
| 3 Tier 1 components, layout, camera | done |
| 4 Solvers, bindings, SceneState, worker protocol | done |
| 5 Hand-authored Archimedes, **Gate A** | done, awaiting Gate A review |

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

## Phase 3: Tier 1 components, layout and camera

- 34 Tier 1 components on top of the 9 primitives (43 total): annotation (arrow, vector_arrow, dimension,
  bracket, label, callout, note, marker, caption), math (equation with per-term ids, readout, graph, axes,
  grid), physics (container: tank/beaker/cylinder/overflow can; block and ball with materials and optional
  faces; rope, spring, pulley, pendulum, balance, spring_scale, pipe, flow_particles, pressure_gauge,
  manometer, particles), engagement (title_card, card, icon, badge, speech_bubble, burst).
- Shared engine layout functions per component (ADR-016); dynamic anchors (equation terms, pipe sections);
  object references resolved during evaluation (ADR-018).
- Layout validation (ADR-017): off-screen, camera crops, overlaps, occlusion, text size at 1080p, contrast,
  caption overlap, clutter. `scripts/check-fixtures.ts` validates whole fixture lessons.
- MathJax: `\mathrel`/`\mathbin` wrapping keeps spacing around per-term parts.
- Gallery fixture lesson (3 scenes) exercising every Tier 1 component.
- Tests: 86 engine (geometry, continuity physics of flow, tabular readouts, TeX allowlist, anchor following,
  relative placement, camera focus, each layout rule), 28 components (25 SVG golden frames), browser pixel
  determinism and PNG goldens for both fixture lessons.

## Phase 4: Solvers, data binding, SceneState, worker protocol

- Solvers (ADR-019): buoyancy (drop / lower / hold, overflow and catch container, spring-scale reading),
  fluid flow (continuity + Bernoulli, per-section outputs), projectile (RK4, optional drag), pendulum (RK4),
  spring-mass (analytic). Outputs bindable as `<solver>.<output>`; attached channels drive objects
  (body y, water level, ripples, outflow, catch level, scale reading).
- Narration vs physics check (`NARRATION_CONTRADICTS_SOLVER`).
- SceneState at any time, WorldRegistry, continuity checks (identity type change, identity drift, abrupt
  disappearance), clean-stage transitions with `keep`.
- Full worker protocol (ADR-020) with Zod-validated params, compile cache, debug CLI
  (`engine validate|compile|state|sample|schema|catalog`).
- Generated contracts: `protocol.schema.json`, `catalog.json`, Pydantic models; typed `EngineClient`.
- Tests: 105 engine (solver physics vs closed forms, continuity), 12 worker, 10 Python contract tests (live vs
  committed contracts, 4 golden RPC fixtures, invalid params, continuity over RPC).

## Phase 5: Hand-authored Archimedes lesson (Gate A)

- `fixtures/scenes/archimedes`: 12 scenes, 274.5 s, written in the DSL with no engine special cases:
  why things float, the block above the water, the drop and splash, displacement, the level rise (camera
  zoom with a live Δh dimension), the displaced volume, weight, buoyant force (balance, then a push
  deeper), the overflow experiment with a spring scale (15.7 N reading, 9.8 N lost = 9.8 N spilled),
  the equation with each symbol explained, a worked example (1000 × 0.001 × 9.81 = 9.8 N computed by the
  engine), and a summary. Four checkpoint questions.
- Every number on screen comes from a solver or a scene value (P3); the narration check agrees with the
  physics. All 12 scenes validate with zero errors and zero warnings, with continuity across scenes.
- Engine and renderer work the lesson needed:
  - Containers draw in two passes, so water and glass sit in front of submerged bodies (ADR-021).
  - Glyph text supports `_{sub}` and `^{sup}`; `bind` actions take a `format` so text channels can show
    live, formatted values such as `Δh = 2.9 cm` (ADR-022).
  - Container `depth` (plan depth for volumes) and `showMark` (start-level mark); buoyancy outputs
    `innerHeightCm`, `containerArea`, `displacedVolumeCm3`, `levelHeightCm`; attached channels start from
    the solver's first sample, so a spring scale reads the weight before the run.
  - `splash` burst, rounded pulse ring, vector labels at 72% of the shaft, `ship` icon.
  - Decorative objects are exempt from the abrupt-disappearance continuity warning.
  - New layout rule `CONNECTOR_CROSSES_LABEL`: arrows, vectors, dimensions and brackets may end at a
    label's edge but not run through it (connector specs expose `strokeLine`). It caught a slanted
    bracket in the overflow scene; readouts gained `minWidth` so stacked readouts line up.
  - One-shot components (bursts) leave the world once they have played, so they are not carried into
    later scenes or listed as visible in the state summary (ADR-024).
- Authoring tools (ADR-023): `check-fixtures.ts [lesson] [--warnings]`, `probe-lesson.ts <lesson> <scene>
  <t> [ids]` (object boxes and values at a time), `scripts/render_keyframes.py` (PNG stills via the real
  render page).
- `make export-demo` now says plainly that MP4 export arrives in Phase 6.
- Found while running the project for review:
  - `make dev` failed on Windows: uvicorn's reload mode picks an event loop that cannot start the engine
    worker subprocess. `animator.api.loop:loop_factory` fixes it (test added).
  - opentype.js's `toPathData` glued "393" and "-0" into "3930", so regular `e`/`é` and semibold `d`
    rendered as solid blobs (the browser stops drawing a path at the first error). Glyph outlines are
    now written by our own serializer, and a test parses every glyph strictly.
- Tests: 117 engine (new: every fixture lesson compiles with no issues, connector crossing, readout
  width, one-shot bursts), 12 worker, 40 components (12 new
  Archimedes SVG golden frames), 3 web, 22 Python, 6 browser e2e (12 new Archimedes PNG goldens, pixel
  determinism across the whole lesson).
