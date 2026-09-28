# Architecture

An LLM acts as the **director** and a deterministic TypeScript engine acts as the **renderer**.
No image or video generation models are used anywhere. This document describes how the pieces fit;
[DECISIONS.md](DECISIONS.md) records why.

## Runtime topology

```
Browser (React)  <-- HTTP + SSE -->  Python API (FastAPI)
  player, playground,                  job runner, storage, OpenAI pipeline,
  /render page                         correction loop, export orchestration
                                          |
                                          +-- JSON-RPC 2.0 over stdio (NDJSON) --> engine worker pool (Node)
                                          |      version | schema.get | catalog.get | scene.lift |
                                          |      scene.validate | scene.compile | scene.stateAt | scene.sample
                                          |
                                          +-- Playwright (Python) + ffmpeg --> headless Chromium running
                                                 the same /render page and <SceneView> as the browser
```

One deployable. The engine worker is a supervised child process, not a service.

## The one rule that everything else serves

`evaluate(compiledScene, t) -> FrameState` is a pure function of the compiled scene and `t` (P1).
The browser player, the headless exporter, the validators and the Python pipeline all go through the
same TypeScript implementation (P2, P8). Python never re-implements DSL rules, layout, solvers or
interpolation; it asks the worker.

## Packages

| Path | Language | Responsibility |
|---|---|---|
| `packages/engine` | TS | DSL (Zod source of truth), LLM-facing schema + `lift`, catalog specs (params, anchors, bbox), themes, text metrics, MathJax typesetting, layout, expression evaluator, solvers, timeline/action compiler, `compile`, `evaluate`, validation, SceneState/continuity, protocol schemas |
| `packages/components` | TS/React | Renderers for every catalog type, `<SceneView>` (FrameState -> SVG) |
| `packages/engine-worker` | TS/Node | JSON-RPC server over stdio + `engine` debug CLI, bundled to `dist/worker.cjs` |
| `apps/web` | TS/React | Player, DSL Playground, `/render` page for export and critics |
| `apps/api` | Python | FastAPI, engine client (worker pool), LLM pipeline (Phase 7+), export orchestration |
| `contracts/` | generated | JSON Schemas (DSL, LLM-facing DSL, protocol, catalog), version manifest, generated Pydantic protocol models (`contracts/python`) |
| `fixtures/` | data | Hand-authored scenes, invalid-DSL corpus, RPC golden fixtures, recorded LLM outputs |

## Data flow for one scene

```
LLM-facing JSON (strict-schema shaped, nullable fields)
   -> lift()            nulls dropped, shorthand expanded, defaults filled  => canonical DSL
   -> validate          schema -> semantic -> (compile) -> layout/geometry  => ValidationIssue[]
   -> compile           layout, timing resolution, solvers, action expansion => CompiledScene (pure JSON)
   -> evaluate(t)       per-frame resolution of tracks, bindings, anchors, camera => FrameState
   -> <SceneView>       FrameState -> SVG (browser player, /render page, keyframe critic)
```

### Compile

1. **Normalize** (logged as `normalization` issues): clamp opacities, sort keyframes, trim float noise.
2. **Semantic checks**: references, ids, limits, TeX allowlist, expressions, solver plausibility.
3. **World setup**: carried-over objects from the previous `SceneState` + declared objects + objects
   declared inline by `create` actions.
4. **Prepare**: text layout from bundled font metrics, MathJax typesetting into a parsed glyph tree.
5. **Layout**: slots, relative placement and anchor pinning resolved in dependency order.
6. **Timing**: cues, beats and action starts (`at` / `after` / `with` + `delay`) resolved to absolute
   seconds by topological sort; cycles are errors.
7. **Solvers**: analytic or fixed-timestep numeric models baked into sampled channels.
8. **Action expansion**: each action becomes segments on per-object channels. Overlapping segments on
   one channel are an error unless additive.
9. **Layout validation**: `evaluate` at beat boundaries and sampled times; bounding boxes are checked
   for bounds, overlaps, text size, contrast and clutter.
10. **End state**: `evaluate(duration)` becomes the scene's `SceneState`, the start of the next scene.

### Channels

Every object has independent channels so that actions compose instead of fighting:
`x y rotation scale opacity` (placement, from `move/rotate/scale/fade/animate`), `presence`
(enter/exit progress with a style, from `create/remove/show/hide`), `draw` and `reveal`
(stroke write-on, text/equation write), `emph` (highlight intensity with a style), colors
`fill stroke text`, and `params.<name>` (numeric or color params, from `transform`, solvers, `bind`).
Segments are `tween`, `sampled` (solver), `follow` (another object's anchor), or `expr` (binding).

## Scene continuity

The engine computes `SceneState = evaluate(scene, duration)`. The next scene starts from it: all
objects that are still present carry over unless `transition_in` starts a clean stage. The
`WorldRegistry` tracks every id ever created with its type and lifecycle, which is how identity drift
(same id, different type) and abrupt disappearance are detected.

## Visual system

Themes are data (`packages/engine/src/theme`). The LLM picks semantic color roles, never hex. Text is
rendered as glyph paths from bundled Lexend metrics, so measurement equals rendering on every machine
and exports never wait on font loading. Equations are MathJax SVG parsed into a safe glyph tree with
per-term groups. Nothing accepts raw SVG strings.

## LLM pipeline and jobs

```
POST /api/generations {topic, audience, duration_min}
  -> GenerationRunner (asyncio; state in var/generations/<id>/ after every step)
       Stage 1  educational planner  -> EducationalPlan (chapters of 45-90 s)
       Stage 2  storyboard, per chapter (next chapter prefetched)  -> scenes with beats, identity, engagement
       Stage 3  animation planner, per scene, from the previous scene's engine end state -> Scene DSL
                -> engine worker compile/validate -> structured issues -> correction (bounded, escalating)
                -> optional critics -> READY, or a flagged simplified scene (DEGRADED)
  -> SSE /api/generations/<id>/events  -> the browser compiles and plays scenes as they arrive
```

`animator.llm` is the only code that talks to a model provider. `LLMClient` has live, fake and
record/replay implementations. Each stage's model chain falls back through `ANIMATOR_MODEL_FALLBACKS`, and
every call is logged with tokens, cost, latency and retries (ADR-027). `animator.pipeline` holds the stages,
the prompt packs (`docs/PROMPTS.md`) and the scene loop (ADR-028). `animator.jobs` holds the store interface
and the runner (ADR-029): no database yet; a SQL store and a queue plug in behind the same interfaces. The
debug page (`/debug/<id>`) shows the whole trace: plan, briefs, attempts with engine issues, DSL and LLM calls.

## Export

`animator.export` (Python) serves the built web app, starts N headless Chromium processes on its
`/render` page and loads the lesson into each; the page compiles it with the same engine as the
player and reports any invalid scene, which stops the export (P4, P5). Each scene becomes one
**segment**: its frames are rendered at scene-local times on all browsers in parallel, handed to one
ffmpeg process strictly in frame order, and verified with ffprobe. Every captured frame mounts a fresh
SVG subtree, so pixels never depend on the previous frame (ADR-006). Segments are cached by a content
key (the scene, every earlier scene, the render settings, the web build and the engine version), so
scenes can be exported as they become final and unchanged scenes are never rendered twice. When all
segments exist they are concatenated without re-encoding, an optional audio track is muxed in, and
the result is verified (frame count, duration, codec, faststart). A failed segment restarts the
browsers and is retried, then reported by scene. Alongside the video go an `.animation.json` bundle
(re-renderable input) and an `.export.json` report (versions, per-scene timings, frame hashes).

Exports that must be reproducible run in the pinned Docker image (`docker/export.Dockerfile`,
ADR-026). There the same stored DSL gives identical frame hashes and a byte-identical MP4 (ADR-025).

## Security boundaries

LLM output is untrusted data: it reaches the renderer only after the worker validated it. No `eval`,
no `Function`, no `foreignObject`, no external URLs, no HTML text. Paths go through a strict parser;
TeX goes through a command allowlist; expressions go through a restricted parser. The worker gets no
secrets and has `fetch` disabled.
