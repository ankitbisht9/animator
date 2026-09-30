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
| 5 Hand-authored Archimedes, **Gate A** | done, Gate A approved 2026-09-25 |
| 6 Video export MVP, **Gate B** | done on the host (Docker deferred by the user) |
| 7 API, jobs, OpenAI infrastructure, planner + storyboard | done (no database, by request: file store) |
| 8 Animation planner, validators, correction loop, critics | done, verified offline; critics off by default |
| 9 End-to-end + eval harness, **Gate C** | built and verified offline; live eval and bake-off blocked: the API key's account is deactivated |
| 10 Progressive generation | done |
| 11 Long-form (chunks, continuity, export) | done offline; a live 15-minute Bernoulli run needs a working key |
| 12 Hardening, debug page, docs, TTS plan | done |

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

## Phase 6: Video export MVP (Gate B)

- `animator.export` (ADR-025): Python service that serves the built web app, runs N headless Chromium
  processes on `/render`, and exports a lesson as one H.264 segment per scene.
  - Frames are rendered at scene-local times, fed to ffmpeg in frame order, and checked with ffprobe.
  - Segments are cached by content key, so scenes can be exported as they become final and interrupted
    exports resume.
  - Segments are concatenated without re-encoding into a verified MP4 (frame count, duration, codec,
    faststart), with BT.709 colour and optional WebM.
  - Each export also writes an `.animation.json` bundle (re-renderable with `--bundle`) and an
    `.export.json` report.
- Failure handling: invalid scenes stop the export and are named. A failed segment restarts the browsers
  and is retried, then reported with its scene. The audio-track hook (`AudioSource`) pads or trims a track
  to the video.
- CLI: `python -m animator.export` (`--lesson | --bundle`, `--scenes`, size, fps, CRF, preset, workers,
  formats, `--audio`). `make export-demo` renders Archimedes. `make export LESSON=...` renders any fixture.
- Deliverable: `var/exports/archimedes/archimedes.mp4`.
  - 1920x1080, 30 fps, 8,235 frames (exactly 274.5 s), H.264 yuv420p BT.709, 15.6 MB.
  - Two independent exports (4 browsers from fixtures, 6 browsers from the bundle) gave identical hashes
    for every frame and a byte-identical MP4.
- Throughput: rendering takes 10-15 ms per frame. The PNG capture was the bottleneck; switching to
  Chromium's fast PNG encoder (same pixels) made capture about 2.5x faster. The whole lesson renders at
  about 10 fps (14 min).
- Pinned Docker environment (ADR-026): `docker/export.Dockerfile` is written, with images pinned by digest
  and tools by exact version, plus Make targets and a CI job. It is **not verified**: building it filled the
  C: drive (where Docker Desktop keeps its disk), and the user chose to skip Docker for now.
  `test_pinned_image_uses_the_pinned_tools` is skipped with this TODO. Linux golden frames are only
  compared inside that image.
- Found and fixed while running the project: `make dev` failed on Windows (uvicorn reload picks an event
  loop that cannot spawn the engine worker), and opentype.js corrupted the `e`, `é` and `d` glyph outlines.
- Tests: 117 engine, 12 worker, 40 components, 3 web, 42 Python (20 new export tests with a fake renderer
  and real ffmpeg), 9 browser e2e (new: determinism with 1 vs 3 browsers, crash-and-retry, colour
  fidelity), 1 skipped (the Docker TODO).

## Phases 7-12: the AI pipeline, progressive playback, long form, hardening

Built locally without Docker or a database, as requested. The seams for scaling are interfaces: a SQL store,
a job queue and a Redis hub plug in without touching the pipeline (ADR-029).

- **Phase 7: API and OpenAI infrastructure** (ADR-027).
  - `animator.llm`: a provider-neutral `LLMClient` with OpenAI, fake and record/replay implementations
    (`ANIMATOR_LLM_PROVIDER`).
  - Per-stage model chains with fallbacks (`ANIMATOR_MODEL_FALLBACKS=gpt-5,gpt-4.1,gpt-4o`). Capabilities live in
    `config/models.json`: reasoning levels are mapped per model, and non-reasoning models get no effort and a
    clamped output limit.
  - Our own retries with backoff, jitter and `Retry-After`. A rejected key, including a deactivated account, and
    empty quota fail fast with a clear message.
  - Refusal, truncation and bad JSON detection. Cost accounting from `config/pricing.json`, and a
    per-generation budget hard stop.
  - Stage 1 (educational planner) and Stage 2 (storyboard, per chapter) with strict schemas from Pydantic models.
  - `GenerationStore` (JSON files under `var/generations/`), `GenerationRunner` (asyncio: persisted after every
    step, resumable, cancellable), and the FastAPI endpoints with SSE.
  - Logging redacts anything shaped like a key.
- **Phase 8: animation planner, validation and correction** (ADR-028).
  - Stage 3 uses the engine's LLM-facing schema from the worker, with the DSL reference, component catalog and
    Archimedes exemplars as a cached static prefix (`docs/PROMPTS.md`).
  - Engine issues go back to the model as structured corrections, with escalation on the final attempt and a
    repair prompt when a reply was unusable.
  - Optional text and visual critics: the visual critic renders keyframes in headless Chromium and sends them to
    a vision model.
  - Degraded scenes are simplified and flagged, and the lesson continues.
- **Phase 9: end to end and evaluation** (ADR-031).
  - Topic to a complete, playable lesson, verified offline with a scripted LLM and the real engine.
  - `python -m animator.eval` / `make eval`: 16 topics across domains; metrics for first-pass validity, fixes,
    degraded rate, time to first play and cost per minute of video; `--set` overrides for a model bake-off.
  - **Blocked:** a live run needs a working OpenAI key. The provided key's account is deactivated, which the
    client reports on the first call.
- **Phase 10: progressive generation** (ADR-030).
  - The lesson page plays scenes as they arrive over SSE, compiling them in the browser with an incremental cache.
  - Pending scenes and chapters show in the list and scrubber, and seeking is limited to what is ready.
  - A "Preparing the next scene" overlay covers buffering, and playback resumes automatically.
  - Optional pacing ("save cost") keeps generation a buffer ahead of the viewer.
  - Cancel, resume after an outage or engine failure, and resume after a server restart.
  - Verified in a headless browser against `scripts/dev_fake_llm.py`.
- **Phase 11: long form.**
  - Chapters of 45-90 s are storyboarded just ahead of generation, with the next one prefetched; continuity
    carries across chapters through the engine's end state.
  - A chapter whose storyboard fails gets one scene outlined from the plan, and later chapters continue.
  - A finished lesson exports to MP4 through the API (per-scene segments, cached, then concatenated).
- **Phase 12: hardening.**
  - Debug page `/debug/<id>`: plan, briefs, attempts with engine issues and critic notes, DSL (opens in the
    Playground), and every LLM call with tokens, cost, latency and retries.
  - Failure injection tests: OpenAI 429/5xx/401/quota/refusal/truncation/bad JSON, budget stop, worker crash on a
    scene (degraded), engine outage (paused), failing storyboard, cancel, restart-resume.
  - Per-scene render error boundary holding the last good frame.
  - `docs/PROMPTS.md`, `docs/TTS.md` (narration audio plan), README usage.
- **Found and fixed:** the storage layer's `list` method shadowed the builtin in type annotations (renamed
  `recent`); EventSource reconnected forever after a finished lesson, so the stream now ends and the client closes
  it.
- **Tests:** 117 engine, 12 worker, 40 components, 4 web, 81 Python, 10 browser e2e (new: an exported MP4 from a
  generated lesson), 1 skipped (Docker TODO). New Python suites:
  - `test_llm.py`: 19 tests against the real SDK over a mock transport;
  - `test_pipeline.py`: 17 with a scripted LLM and the real engine;
  - `test_generations_api.py` (HTTP and SSE) and `test_eval.py`.

## First live runs (working key)

- **Bug found live:** OpenAI rejected the scene schema ("`$ref` cannot have keywords"), so every scene failed
  at once. The schema projection now emits bare references; all four stage schemas were verified as accepted.
- **Timeouts:** scene calls take 1.5-2.5 minutes at high effort, so a 120 s timeout caused retries (one scene
  spent 10 minutes). The timeout is now 600 s.
- **Engine bug found live:** a solver in a later scene could not use a pipe carried over from an earlier one
  (validation only), which forced a correction in every such scene. Fixed.
- **Results for four 30-second Bernoulli lessons, as the fixes landed:**

  | Run | Time to complete | First scene playable | Cost | Scenes passing first time |
  |---|---|---|---|---|
  | 1 | 8.4 min | 2.4 min | $0.58 | 0 of 3 |
  | 2 (timeouts) | 23 min | 12.6 min | $0.45 | 0 of 2 |
  | 3 | 7.1 min | 3.0 min | $0.51 | 1 of 3 |
  | 4 | 6.6 min | 3.1 min | $0.28 | 2 of 3 |

  In run 4 the instruction prefix was already in OpenAI's cache from the earlier runs.
- **Where the cost goes:** each scene call sends about 49k tokens of instructions (DSL reference, component
  catalog, examples; cached after the first call at 10% of the price) and gets about 5-6k tokens back, over half
  of it reasoning. A first scene call costs about $0.15, later ones about $0.06-0.07, and corrections
  $0.03-0.12. The plan (about $0.01) and storyboards (about $0.04 each) are small.

## Cost reduction (ADR-032)

- Measured on the same 30-second lesson:
  - gpt-6-sol / high: $0.28-0.58 and 6.6-8.4 minutes.
  - gpt-6-luna / medium: **$0.019 and 3.7 minutes**, first scene playable after 84 s, 2 of 3 scenes right
    first time.
- Defaults are now Luna for every stage, Sol only as the final escalation, and cheap fallbacks
  (gpt-5-mini, gpt-4.1-mini). Prices were verified on the OpenAI pricing page.
- Quality guards for the cheaper model:
  - new layout error `TITLE_COVERED` (stage content over the scene title);
  - equations count in the occlusion check;
  - prompt v3: one of at/after/with per action, nothing above the stage line, gauges pinned to pipe sections,
    compact scenes.

## Speed: generation keeps up with playback (ADR-033)

Target: a lesson generates in no more time than it plays. A 30-second lesson used to take 3.7 minutes.
- **Parallel scenes** (default `ANIMATOR_SCENE_MODE=parallel`): every scene starts on a clean stage, so all
  scenes are drawn at once. Long lessons storyboard all their chapters at once.
- **One streamed outline call** for lessons up to 150 s (plan and storyboard, `outline.v2`). Scene 1 starts
  about 4 s in, while the later briefs are still being written.
- **Low reasoning effort** everywhere, and the **priority tier** (`OPENAI_SERVICE_TIER=priority`, about 1.6x
  faster output).
- **Patch corrections**: a correction returns only what changes, in 5-10 s instead of about 20 s.
- **Sure fixes without the model** (`pipeline/tidy.py`): ambiguous starts, time references, TeX aliases, late
  actions, out-of-range numbers, and bare anchor ids. Also objects off the frame, over the title or on another
  label, moved as the engine's new `fix` hint says.
- **Hedged drafts**: two drafts per scene, and two patches per correction round. The first valid result wins,
  and a failed draft's correction races the other draft.
- **Fresh scenes** (`transition_in.fresh`, set by the pipeline): the player and exporter compile each parallel
  scene on its own, so an id reused for a different object no longer breaks playback.
- **Frame check fixes:**
  - "CO₂"-style subscripts drew as "?"; they now render as real subscripts.
  - Text clipped by the frame edge is now an error; it used to pass when under 10%.
- **Results (final build, 14 live 30-second lessons, 7 topics):**
  - 21-33 s in total, median about 26 s; 13 of 14 were within 30 s.
  - The first scene is playable after 14-25 s.
  - Each costs $0.020-0.054, with a median of $0.030. That includes the losing drafts, which finish after the lesson is ready.
  - 1-minute lessons take 28-32 s.
  - All scenes play in the browser. See ADR-033.


## Visual relevance (ADR-035)

- 80 recognisable icons (Lucide, ISC): people, hands, hearts, nature, animals, lab and tech objects, everyday
  things. The prompt (`animation.v4`) makes the model use them instead of building figures from shapes.
- New engine checks: arrows running through objects, and arrows crossing each other. The engine suggests the
  smallest new route: a bend, or both ends over or under.
- A polish pass on every valid scene, with no model call: labels moved off objects, arrows rerouted, content
  lifted from under the caption, undrawable emoji removed.
- Results on 21 new scenes against 116 old ones:
  - labels across objects in 14% of scenes (was 27%);
  - nothing under the caption, and no "?" glyphs;
  - 1.7 icons per scene (was 0.4);
  - 10 of 12 drafts valid first time (was 7 of 12).
- Speed and cost are unchanged.

## Narration voice (ADR-037)

- Every caption is spoken, and each word lights up when the voice says it, in the player and in exported MP4s.
- One switch: `ANIMATOR_TTS_ENABLED` (1/0). With 0, nothing changes anywhere.
- Swappable providers:
  - `edge` (default): free, no key;
  - `azure`, `google`, `elevenlabs`: free tiers, keys in `.env`;
  - `openai`: OpenAI, or a free local Kokoro server.
- Speech is synthesized while the scene is written, and cached by content. Beats stretch to fit the speech, and
  the engine validates the retimed scene. Speech failures leave captions only.
- Live results:
  - 30-second lesson ready in 25 s, 1-minute lesson in 29 s;
  - LLM cost unchanged; speech is free;
  - the voice step adds under 0.1 s per scene.

## Kokoro voice and Hinglish lessons (ADR-038)

- `kokoro` provider: a free local voice with exact word timings. Defaults: `af_heart` for English and `hm_omega`
  for Hinglish.
- A Language choice on the create form: English (prompts unchanged) or Hinglish (Roman script, English technical
  terms), each with its own voice (`ANIMATOR_TTS_VOICE_HINGLISH`).
- Voice failures and `.env` mistakes are shown in the app instead of silently leaving captions only.
- `scripts/tts_samples.py`: listen to the voices before choosing.
