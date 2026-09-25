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

## Export

The Python export service opens the web app's `/render` page in headless Chromium and calls
`window.__renderFrame(frame)` (`t = frame / fps`, integer arithmetic). Each frame mounts a fresh SVG
subtree so paint output never depends on the previous frame (see ADR-006). Frame ranges are split
across browser instances and piped to ffmpeg.

## Security boundaries

LLM output is untrusted data: it reaches the renderer only after the worker validated it. No `eval`,
no `Function`, no `foreignObject`, no external URLs, no HTML text. Paths go through a strict parser;
TeX goes through a command allowlist; expressions go through a restricted parser. The worker gets no
secrets and has `fetch` disabled.
