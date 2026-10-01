# Architecture decision records

Short ADRs. Status is `accepted` unless noted. Newest decisions are appended at the end.

---

## ADR-001 Monorepo tooling

**Decision.** One repo. pnpm workspaces for TypeScript (`apps/web`, `packages/*`), a uv workspace for
Python (`apps/api`, `contracts/python`), GNU make as the root task runner. Exact versions are pinned
(`save-exact` + `pnpm-lock.yaml`; `uv add --bounds exact` + `uv.lock`). Lint: Biome + `tsc` for TS,
Ruff + mypy `--strict` for Python. Python 3.14, Node 24, TypeScript 7 (native compiler).

**Why.** Matches the spec. Biome replaces ESLint + Prettier with one pinned binary. Makefile recipes only
call `pnpm`/`uv`/`node`/`git` so they run identically under cmd.exe (make on Windows) and POSIX sh.

---

## ADR-002 Engine worker protocol: NDJSON JSON-RPC 2.0 over stdio

**Decision.** One JSON-RPC message per line on stdin/stdout, diagnostics on stderr. The worker is bundled
by esbuild into `dist/worker.cjs` (no runtime `node_modules` lookup). Python runs a pool of workers with
per-call timeouts; a timed-out worker is killed (it cannot be interrupted mid-compute) and restarted;
idempotent calls are retried once on a fresh worker; a restart budget turns repeated failure into
`EngineUnavailable`, which the pipeline maps to a DEGRADED scene. The first call is a `version` handshake;
a protocol **major** mismatch is refused.

**Why.** Stdio needs no ports, no auth and no network. Line framing is trivial in both languages.
The Python stream limit is raised to 64 MB so large compiled scenes fit on one line.

---

## ADR-003 No database for now

**Decision.** At the user's request there is no database in v1. Storage goes through a small repository
interface with two backends: `memory` (default) and `files` (JSON run records under `ANIMATOR_DATA_DIR`
for inspection). SQLite + Alembic (then Postgres) remain the planned durable backend and slot in behind the
same interface later.

**Consequences.** "Resume after server restart" (Section 13) only works with the `files` backend and
is weaker than a transactional store; this is revisited when the scheduler lands (Phase 10).

---

## ADR-004 Text is rendered as glyph paths from bundled font metrics

**Decision.** Bundle **Lexend** (SIL OFL, via `@fontsource/lexend`). A build script extracts per-glyph
advance widths, kerning pairs and outline paths into JSON committed with the engine. Text layout
(measurement, wrapping, alignment) runs in the engine; renderers draw glyph paths. There is no runtime
font loading and no DOM measurement.

**Why.** Deterministic measurement equal to what is drawn, on every machine, in the browser, the worker
and headless Chromium. It removes a whole class of export flakiness (font not yet loaded). Lexend was
designed to improve reading fluency, which serves the struggling readers this product targets.

**Trade-off.** No complex shaping (ligatures, RTL). Characters outside the bundled set are a validation
issue rather than silently substituted.

**Amended in Phase 5.** Glyph outlines are written by our own integer serializer in `build-fonts.ts`,
not opentype.js's `toPathData`, which corrupted glyphs whose coordinates round to -0. A test parses
every glyph in every table with the engine's strict path parser.

---

## ADR-005 Spike: MathJax SVG determinism

**Setup.** `mathjax-full@3.2.2`, TeX input (`base`, `ams`, `html`), SVG output with `fontCache: "none"`
(glyphs as inline paths, no font dependency). Seven expressions including Bernoulli's equation and
`\cssId` term wrappers. Hashes compared across repeated runs and a fresh process.
(`packages/engine/scripts/spikes/mathjax_determinism.ts`)

**Result.** Identical output within a process and across processes. Cost: ~40 ms cold for the corpus,
~2 ms per expression warm.

**Decision.** Typeset at compile time, cache by TeX string, parse the SVG into a structured glyph tree
(`g`/`path`/`rect` with validated transforms) so renderers never receive SVG strings. Per-term ids come
from wrapping each equation part in `\cssId`. User TeX goes through a command allowlist, and user input
can never use `\cssId`, `\href`, `\style`, `\class`, `\require` or macro definitions.

---

## ADR-006 Spike: export throughput and determinism (Python Playwright + ffmpeg)

**Setup.** A test page with ~150 animated SVG objects (gradients, a blur filter, paths, text),
1920x1080, Chromium headless via Playwright for Python 1.63, PNG screenshots piped to ffmpeg
(H.264, yuv420p, CRF 18, faststart). Frame ranges split across N browser instances.
(`scripts/spikes/export_throughput.py`, 12-core Windows dev machine.)

**Result.**

| Browsers | Frames/s |
|---|---|
| 1 | 9.8 |
| 2 | 17.1 |
| 4 | 34.7 |

Encoding 180 frames took 1.7 s. A 5-minute video at 30 fps (9,000 frames) renders in about 4-5 minutes
with 4 browsers.

**Determinism finding.** Updating the SVG in place was *not* deterministic: 91 of 180 frames differed
between two runs (max 13/255, in thin strips at the edge of animated shapes). Chromium repaints only
damaged regions, so anti-aliasing depended on which frame was painted before. That violates P1 at the
pixel level. Mounting a fresh clone of the SVG subtree for every captured frame fixed it: **0 of 180
frames differ.**

**Decision.** The `/render` page mounts a fresh `<SceneView>` subtree per captured frame (React `key` =
frame number). Chromium flags: `--disable-gpu --force-color-profile=srgb --font-render-hinting=none
--disable-lcd-text`. Scale with more browser instances before considering WebCodecs. Remotion is not
needed.

---

## ADR-007 Spike: OpenAI strict Structured Outputs viability

**Setup.** The LLM-facing schema is *generated* from the canonical Zod DSL by a projection
(`packages/engine/src/dsl/llm-schema.ts`): every property required (optional becomes nullable, `null` =
default), `additionalProperties: false` everywhere, `oneOf` -> `anyOf`, `const` -> single-value enum,
range/length/pattern/format/default keywords removed (the engine validates them), and `x-llm`
overrides (color references become an enum of theme roles). `lift()` maps it back (drop nulls, expand
shorthand, apply defaults) and is idempotent.

**Static result** (`packages/engine/scripts/spikes/strict_schema_check.ts`, run against the full schema
with every component registered at the time): object root, all objects strict, only supported keywords,
492 properties (limit 5000), max object nesting 4 (limit 10), 5,483 schema characters (limit 120,000),
300 enum values (limit 1000). These limits are the previously documented ones; OpenAI's docs pages
fetched on 2026-09-25 did not restate them, so the live probe is the real test.

**Live result.** Pending: no API key yet. `uv run python scripts/spikes/openai_strict_probe.py` sends one
strict request with the generated schema and reports acceptance, refusal, truncation and usage.

**Decision.** Single-call Stage 3 in strict mode. Field order is chosen for generation quality (strict
mode generates in schema order): scene intent and narration before objects, objects before the
timeline, and `id`/`type`/`params` first within an object. Fallbacks if the live probe fails: split Stage
3 into 3a (objects, layout) and 3b (timeline), then non-strict JSON + engine validation + the correction
loop. Refusals and `status: incomplete` always count as failed attempts.

---

## ADR-008 Component specs live in the engine; renderers live in `components`

**Decision.** The spec's single `defineComponent({...})` contract is split in two halves joined by
`type`. `packages/engine` owns the **spec** (params schema, defaults, anchors, bbox, semantic roles,
description, examples) because the worker needs it for layout and validation and must not import React.
`packages/components` owns the **renderer**. A test fails if any catalog type has no renderer or vice
versa. Themes are data in the engine (validators need contrast and color math); components read them.

---

## ADR-009 Engagement layer

**Context.** The product goal is to hold the attention of every kind of student, including students who
struggle, and to feel interactive.

**Decision.**
- *Visual richness, still deterministic:* material-aware objects (wood grain, metal sheen, ice, stone),
  glass containers with a liquid gradient, surface shimmer, ripples and seeded bubbles, soft shadows,
  springy entrances (back/spring easing), glow highlights, celebration bursts, friendly icons, and an
  optional expressive face on bodies for younger audiences. Ambient motion is a pure function of `t`.
- *Readable captions:* the caption bar highlights each word as it is read (karaoke timing derived from
  the beat's duration), with `**emphasis**` rendered in the accent color. Lexend throughout (ADR-004).
- *Interactivity in the player (not in exported video):* hover or tap any object to see what it is and
  its live bound values, click a beat in the transcript to jump to it, and **checkpoint questions**
  (`interactions` in the DSL) that pause playback for a quick multiple-choice check with an explanation.
- None of this changes P1: interactions live in player UI state, never in `evaluate`.

---

## ADR-010 Browser automation in Python only

**Decision.** Golden-frame, e2e and export tests use Playwright **for Python** (pytest). The Node
Playwright test runner is not used.

**Why.** One browser download and one automation stack. The exporter and the tests share the static
server and the `/render` driver code.

---

## ADR-012 The expression evaluator is an engine module

**Decision.** The restricted expression parser/evaluator lives in `packages/engine/src/expr` rather than
a separate `packages/expr`. It is a Pratt parser over a whitelisted grammar (numbers, identifiers,
`+ - * / ^ %`, parentheses, whitelisted functions). No `eval`, no `Function`. Length and depth are limited.

**Why.** It has no consumers outside the engine; one package fewer to version.

---

## ADR-013 Objects are declared once; actions refer to them

**Decision.** Every object lives in `objects`. `create` refers to an object id instead of carrying an inline
`objectSpec`. An object whose first presence event is a revealing action (`create`, `show`, `draw`,
`write`, `transform.into`, a `label`) starts hidden; otherwise it is visible from the start. `label` and
`group` actions synthesize their objects.

**Why.** An inline spec duplicates the whole object union inside the action union, roughly doubling the
LLM-facing schema. One place per object also makes identity checks trivial.

---

## ADR-014 Channels, not one track per action

**Decision.** Each object has independent channels (`x y rotation scale opacity`, `presence` with an
entrance/exit style, `draw`, `reveal`, per-term reveal, `emph` with a highlight style, colors,
`params.*`, `morph`). Actions compile to segments on channels (`tween`, `color` in OKLab, `step`, `keys`,
`solver`, `expr`, `follow`, `sampled`, `presence`, `pulse`). Overlapping segments on one channel are a
`TRACK_CONFLICT`. Entrance, exit and emphasis effects apply about the visual center and never move
layout geometry: anchors, followers and placement use the effect-free matrix, so an arrow attached to a
popping block does not wobble.

A tween starts from the channel's value at the action's start, read from the scene compiled so far.
Dynamic segments (`expr`, `follow`) get their end value computed at compile time, so playback never
evaluates a moment other than the one being drawn. Camera follow is smoothed at compile time and
baked into samples.

---

## ADR-015 Timing and normalization rules

**Decision.** Actions without `start` run after the previous action (`lift` makes this explicit). `follow`
and `bind` without a duration last until the scene ends. Beats without `at` (and without a cue naming
them) are spread evenly between their anchored neighbours. Write-on durations scale with text length
(0.035 s per character, clamped). The only silent repairs are clamping object opacity, sorting keyframes
and trimming times to 3 decimals, each reported as a `normalization` issue.

---

## ADR-011 Golden frames at two levels

**Decision.**
1. **SVG golden frames** (Vitest, `packages/components/test/__snapshots__`): `<SceneView>` rendered to static
   markup at fixed times for fixture scenes. Platform-independent (no rasterization), so they run in CI
   everywhere and catch any change in structure, geometry or color.
2. **PNG golden frames** (pytest + Playwright, `apps/api/tests/golden/<platform>/`): the real `/render` page
   in Chromium, compared with a tolerance (< 0.2% of pixels differing by more than 8/255). Rasterization
   differs slightly between operating systems, so goldens are per platform. Windows goldens are committed;
   Linux goldens will be generated inside the pinned Docker export environment (Phase 6). Until then the
   Linux comparison is skipped with an explicit reason.
3. **Pixel determinism**: the same frames rendered in forward and reverse order must hash identically.

---

## ADR-016 One layout function per complex component

**Decision.** Complex components (vector arrows, containers, pipes, readouts, equations, cards, gauges...)
expose a `layout*` function in the engine. The spec's `geometry` (bounds and anchors, used for placement,
followers and validation) and the React renderer both call it. The renderer never computes its own geometry.

**Why.** P8 applied to drawing: what the validator checks is exactly what is painted. It also makes anchors
such as `tank.water_surface`, `eq.term_rho` or `pipe.section_throat_top` exact, so arrows, gauges and labels
attach to the right pixel as things move.

---

## ADR-017 Layout validation checks what the viewer sees

**Decision.** After compiling, the engine evaluates the scene at beat boundaries, action ends and one-second
samples and checks settled objects (entrances in progress are skipped) after the camera transform:

| Code | Severity | Rule |
|---|---|---|
| `OUT_OF_BOUNDS` | error | more than 10% of an object outside the 1600x900 world frame |
| `CAMERA_CROPS` | warning | the camera leaves an object out of view (often intentional) |
| `NEAR_EDGE` | warning | non-decorative content touches the frame edge |
| `LABEL_OVERLAP` | error | two label-like objects overlap by more than 12% |
| `LABEL_OCCLUDES` | warning | a label covers a large part of an object it is not attached to |
| `TEXT_TOO_SMALL` | error below 80% of the minimum, else warning | smallest text in an object below 22 px at 1080p (includes object scale and camera zoom) |
| `LOW_CONTRAST` | warning | text drawn directly on the background below 3:1 |
| `UNDER_CAPTION` | warning | content hidden behind the caption bar |
| `CLUTTER` | warning | more than 12 non-decorative objects visible at once |

Each component declares its smallest text size and whether its text sits on the background in one table
(`catalog/legibility.ts`). Errors make a scene invalid (correction loop, then a flagged degraded scene);
warnings go to the critics.

**Amended in Phase 5.** `CONNECTOR_CROSSES_LABEL` (warning): a connector whose stroke passes through the
inside of a label-like object (6 px clearance, so ending at `label.left` is fine). Connector specs provide
`strokeLine` (points along the stroke, from the same layout function the renderer uses). Bounding-box
overlap cannot catch this case, because a connector's bbox always overlaps what it points at.

---

## ADR-018 `flow_particles` reads the pipe it flows through

**Decision.** `flow_particles` is its own component that references a pipe (`pipe: <id>`). Object params
resolve during evaluation to the referenced object's layout bounds, matrix and params, so the particles
use the pipe's exact geometry. Particle positions are a pure function of `t`: a travel-time table built from
continuity (speed ∝ 1/width²) is inverted per particle with a seeded phase and lane.

---

## ADR-019 Buoyancy: exact statics, stylized kinematics

**Decision.** At every sample the buoyancy solver computes the exact static state for the body's current
height: the water level from volume conservation (`A·L − V_sub(L − b) = V_water`, solved by bisection),
submerged volume (box or spherical cap), buoyant force `ρ_f·V_sub·g`, apparent weight, and overflow into a
catch container. The path the body takes is stylized for teaching: a short fall, then a damped bob that ends
exactly at equilibrium (floating), a gentle sink to the floor, or a smooth lowering on a spring scale.
Ripples fade out before the run ends so end states are calm.

**Why.** A rigid-body engine would drop the block into the water in a fraction of a second and would not give
clean buoyancy. Here every displayed number is physically exact for the picture on screen, and the motion
has time to be understood. The container is treated as square in plan (depth = inner width) and a block's
depth equals its width; `unitsPerMeter` converts pixels to meters in one place.

Checked against closed forms in tests: floating fraction `ρ_b/ρ_f`, `F_b = W` at rest, level rise `V_sub/A`,
spilled weight equals buoyant force (Archimedes), spring-scale drop equals `F_b`, and the same for the
projectile range, pendulum and spring periods, and Bernoulli's energy balance.

---

## ADR-020 Worker protocol details

**Decision.** Methods: `version`, `health.ping`, `schema.get`, `catalog.get`, `scene.lift`, `scene.validate`,
`scene.compile`, `scene.stateAt`, `scene.sample`. Every scene method lifts first (so LLM-facing and canonical
input are both accepted), carries `prevState` for continuity, and reports scene problems as structured
issues in a normal result; JSON-RPC errors are reserved for malformed requests (`-32602`) and engine faults.
`scene.compile` returns the compiled scene (opaque to Python), the engine-computed end state and a compact
state summary for the next LLM call. Compilation is cached in the worker by a content hash (an optimization
only; calls stay stateless and idempotent).

Message schemas are Zod in `packages/engine/src/protocol`, exported to `contracts/protocol.schema.json`, and
turned into Pydantic models (`contracts/python`, datamodel-code-generator with a pinned formatter). Contract
tests assert that live worker answers equal the committed contracts and replay golden request/response
fixtures from `fixtures/rpc`.

---

## ADR-021 Containers draw in two passes

**Decision.** A component may register a second renderer in `FRONT_RENDERERS`. `SceneView` draws each layer
in order: every object's normal pass, then the front passes of that layer. The container's front pass
draws a translucent veil of the liquid clipped to the inner walls, the surface line, the glass walls, the
ticks and the spout. Its back pass draws the liquid body, bands and bubbles.

**Why.** A block or stone inside a tank must look submerged: the part under the surface should be tinted
by the water and the glass should be in front of it. Painter's order can't put a body both in front of the
liquid and behind the glass when the liquid and the glass belong to one object, and asking the LLM to split containers into several objects
would break identity (P6). The split lives in the renderer, uses the same `layout*` geometry as the
spec (ADR-016), and adds nothing to the DSL. Objects on the `foreground` layer (force arrows) still draw
over everything.

---

## ADR-022 Sub/superscripts in text, formatted bindings

**Decision.** Glyph text markup gains `_{…}` and `^{…}` (0.7× size, shifted baseline) next to `**emph**`.
The layout stays deterministic and per-glyph (size and `dy` per placed glyph). A `bind` action takes an
optional `format` (`"Δh = 0.0 cm"`, the same number formats readouts use). The engine formats the value
when it samples the channel, and the segment's end value stays a raw number.

**Why.** Physics labels need `F_b` without switching to an equation object, and live values belong
inside sentences and labels as well as in readouts. The engine does the formatting, so the number shown
comes from the solver (P3). No string templating is exposed.

---

## ADR-023 Fixture lessons are held to zero warnings

**Decision.** Every lesson in `fixtures/scenes` must lift, validate and compile, scene by scene with
continuity, with no errors and no warnings. A warning that a fixture triggers on purpose is listed in
`packages/engine/test/fixtures.test.ts`, which enforces this. The authoring loop is `check-fixtures.ts`,
then `probe-lesson.ts` (resolved boxes and values at a time), then `render_keyframes.py` (PNG stills from
the real render page), then fixes.

**Why.** The Archimedes lesson is the quality bar the LLM pipeline is measured against (Gate A), and the
validators are what the correction loop will rely on. If hand-authored scenes need warnings silenced,
either the scene or the rule is wrong, and each such case was resolved as one or the other. Examples:
per-scene titles instead of one drifting title, and decorative objects exempt from the abrupt-disappearance
check.

---

## ADR-024 One-shot components leave the world when they finish

**Decision.** A component spec can be `oneShot` (bursts are). Once one has been created and has fully played
(`reveal` = 1), its end state is `removed`. It is then never carried into the next scene and never appears
as visible in the SceneState summary.

**Why.** A burst is an event. Before this change, a finished confetti burst stayed "visible" in the
world and was carried from scene to scene as an empty group. The LLM would have read about invisible
objects in the state summary, or would have had to remember to remove every burst by hand. The engine
knows the component plays once, so it handles this itself (P3).

---

## ADR-025 Export: per-scene segments, ordered parallel rendering, verified output

**Decision.**
- **Segments.** One H.264 segment per scene covers global frames `[frame(start), frame(end))`, rounding
  half up. Segments tile the lesson exactly. Each frame is rendered at a *scene-local* time (`__renderAt`),
  so a segment depends only on its own scene and the earlier scenes whose end state it inherits.
- **Caching.** A segment is reused when its key matches: sha256 of the scenes up to it, the render settings
  (size, fps, CRF, preset, formats), the web build id and the engine version. Scenes can therefore be
  exported as they become final, and editing scene k re-renders k and later scenes only.
- **Parallelism.** N browser processes render the frames of a segment concurrently from a shared cursor.
  They are fed to a single ffmpeg process strictly in frame order, through a bounded look-ahead window.
- **Encoding.** libx264 `yuv420p` at the configured CRF and preset with faststart. RGB is converted with the
  BT.709 matrix to limited range and tagged BT.709, so colours match the browser in standard players.
  `+bitexact` keeps version strings out of the container. Segments are joined by the concat demuxer
  without re-encoding. WebM (VP9) is optional, encoded by the same ffmpeg process as a second output.
- **Fail loudly.** Export refuses a lesson with any invalid scene and names it. Each segment and the final
  file are checked with ffprobe (frame count, size, duration, codec, pixel format, faststart). A failed
  segment deletes its partial files, restarts every browser and retries (`segment_retries`, default 2).
  After that it raises `ExportError` with the scene's index and id. Finished segments are kept.
- **Audio hook.** `AudioSource.track(lesson, duration, workdir) -> Path | None`. The track is muxed as AAC
  and padded with silence or trimmed to the video's exact duration. The future TTS provider implements
  this.
- **Outputs.** `<name>.mp4`, `<name>.animation.json` (bundle: title, versions, themes, plan, storyboard,
  assets, scenes; `--bundle` re-renders it) and `<name>.export.json` (settings, versions of engine,
  Chromium, ffmpeg and web build, per-scene frames, attempts, time and fps, per-frame hash digest, and
  output probes).

**Why.** Scene-local rendering and content keys make segments self-contained, so they can be exported
progressively (Phases 10 and 11) and cached. Ordered feeding keeps one encoder per segment, which gives
exact frame counts and no seams inside a scene. Verifying with ffprobe turns a silently short or broken
video into an error.

**Evidence (Windows dev machine).**
- **Tests.** Exporting the same lesson with 1 browser and with 3 browsers gives identical per-frame hashes
  and a byte-identical MP4. A browser killed mid-scene is restarted, the scene is re-rendered, and the
  output is byte-identical to a clean run. A frame decoded from the MP4 as players decode it (BT.709) is
  within 3/255 per channel of the browser's own PNG (`tests/test_export_e2e.py`).
- **Full Archimedes lesson.** 12 scenes, 274.5 s, 8,235 frames at 1920x1080 and 30 fps. Two independent
  exports gave identical per-frame hashes for all 8,235 frames and a byte-identical 15.6 MB MP4:
  - run A: 4 browsers, from the fixtures;
  - run B: 6 browsers, re-rendered from the exported `.animation.json` bundle.

  An earlier export was assembled from two sessions (six cached segments from an interrupted run) and
  used the slower capture below. It produced the same MP4 bytes too.

**Throughput.** Evaluating and painting a frame takes 10-15 ms. The cost is the PNG capture:
- `page.screenshot` takes 130-290 ms at 1080p.
- CDP `Page.captureScreenshot` with `optimizeForSpeed` gives identical pixels, and identical bytes run to
  run, in 40% of the time. The exporter uses it.

The full lesson renders at about 10 fps (about 2.8x real time, 14 minutes), and 6 browsers are no faster
than 4 because the machine is CPU-bound. Next steps, when needed: 720p or a faster x264 preset for
previews, then raw frame capture, then in-browser WebCodecs (spec Section 15).

---

## ADR-026 Pinned export environment (Docker). Status: written, not yet verified

**Decision.** `docker/export.Dockerfile` is a two-stage image:
- **Build stage.** Node 24.11.1 (by digest) and pnpm 10.34.5 build the web app and the engine worker from
  the lockfile.
- **Runtime stage.** Python 3.14.7 (by digest) with uv 0.12.19 (`uv.lock`), ffmpeg 7.1.5 and DejaVu 2.37
  (exact Debian 13 versions), and the Chromium headless shell pinned by Playwright 1.63.0. It also holds the
  built web app and worker, the same Node binary, the fixtures and the tests.
- **Offline runtime.** Nothing is fetched after build. Make targets run it with `--network none` and 2 GB
  of `/dev/shm`.
- **Targets.** `make export-demo-docker`, `make test-docker` (the whole offline suite, including export
  determinism and the Linux golden frames), `make goldens-docker`. A CI job builds the image and runs the
  suite inside it.
- **Linux golden frames.** They are only compared when `ANIMATOR_PINNED_ENV=1`, which the image sets. On
  any other Linux machine the comparison is skipped with a reason.

**Why.** Frame hashes depend on the exact Chromium, font rasterization and ffmpeg build. Pinning all of
them in one image is what makes "same DSL, same frames" hold across machines, not just across runs.

**Status.** The image has **not** been built successfully yet. On the development machine the build filled
the system drive (Docker's disk image is on C:). At the user's request, Docker is skipped for now.
- `make export-demo` renders on the host instead.
- `test_pinned_image_uses_the_pinned_tools` is skipped with this TODO until `make test-docker` has run.
- Determinism is proven on the host (ADR-025) but not yet across machines.
- Before building again, move Docker Desktop's disk image off the full drive (Settings > Resources >
  Advanced > Disk image location). The build needs about 6-8 GB.

---

## ADR-027 LLM layer: provider-neutral client, model fallbacks, our own retries

**Decision.** `animator.llm` defines `LLMClient.generate(LLMRequest, CallContext) -> LLMResult` and the
provider-neutral types. It has three implementations: `OpenAILLM` (Responses API, strict JSON-schema
output), `FakeLLM` (scripted, for tests), and `RecordingLLM`/`ReplayLLM` (fixtures keyed by a hash of the
request content). `ANIMATOR_LLM_PROVIDER=openai|record|replay` selects one; nothing outside the package imports
`openai`.

- **Model chain per stage.** The stage's model comes first, then `ANIMATOR_MODEL_FALLBACKS`
  (default `gpt-5,gpt-4.1,gpt-4o`). A model the key cannot use (404, 403, `model_not_found`) is skipped and
  remembered.
- **Capabilities.** `config/models.json` says which models take a reasoning effort and at which levels. Unsupported
  levels map to the nearest one, and non-reasoning models get no effort parameter and a clamped output limit. A
  400 about an unsupported `reasoning` parameter is retried once without it.
- **Retries** are ours, not the SDK's (so they are counted and logged): exponential backoff with jitter on
  429/5xx/timeouts, honouring `Retry-After`. A rejected key (401, including a deactivated account) and
  `insufficient_quota` are never retried.
- **Failed attempts.** Refusals, `incomplete` responses and non-JSON output raise and count as failed attempts.
  Every call, successful or not, is reported with usage and cost (`config/pricing.json`, longest-prefix match for
  dated snapshots). The per-generation `Budget` is checked before each call: a hard stop.

**Why.** The key given during development belonged to a deactivated account, and model availability differs
between accounts. Fallbacks and a clear, non-retried auth error make both situations visible and recoverable,
instead of slow failures.

---

## ADR-028 Scene loop: engine-validated drafts, bounded corrections, flagged fallbacks

**Decision.** For each scene:
1. The animation planner drafts it.
2. The engine worker compiles it with the previous scene's end state.
3. Errors go back as structured issues to a correction model for up to `ANIMATOR_MAX_CORRECTION_ATTEMPTS` (3).
   The last attempt escalates to `ANIMATOR_MODEL_CORRECTION_FINAL`. A reply that was unusable (refused,
   truncated, malformed) gets a repair prompt instead, and counts as an attempt.
4. Optional critics (text summary, or rendered keyframes for the vision model) can spend one more attempt on
   "revise" with major issues.
5. If every attempt fails, the scene becomes a clearly flagged simplified scene: title, key idea, narration as
   captions, and a "Simplified scene" badge on a fade transition (DEGRADED), and the lesson continues.
6. A scene that repeatedly crashes the worker (after the pool's restart-and-retry) is degraded the same way. If
   even the fallback cannot compile, the engine is down and the generation pauses.
7. A chapter whose storyboard keeps failing gets one scene outlined from the plan.

The pipeline pins `id` and `dsl_version`. Everything else in a scene is the model's, and only the engine
validates it (P4, P8).

---

## ADR-029 Jobs without a database: file store and an asyncio runner

**Decision.** Following ADR-003 and the user's request, there is no database yet. `GenerationStore` is an
interface. `FileStore` (the default) writes one JSON document per generation atomically, plus an append-only
`calls.jsonl`, under `var/generations/<id>/`; `MemoryStore` is used in tests. `GenerationRunner` is the
asyncio job runner:
- plan, then per-chapter storyboards (the next chapter is prefetched while the current one's scenes generate),
  then scenes in order;
- persistence after every step, and live events for SSE;
- cancel, and resume (`paused` after an outage or engine failure; everything in progress on a restart). Ready
  scenes are never regenerated;
- concurrency limits (`ANIMATOR_GENERATION_CONCURRENCY`, `LLM_MAX_CONCURRENCY`);
- optional pacing (`paced`: stay `ANIMATOR_BUFFER_TARGET_SECONDS` ahead of the viewer's reported playhead).

**Scaling path.** A SQL store (SQLite, then Postgres) implements `GenerationStore`. A queue (Arq, Celery, RQ)
replaces `GenerationRunner` behind the same `start/cancel/resume/playhead` surface. The pub/sub `Hub` moves to
Redis when there is more than one API process. The pipeline does not change.

---

## ADR-030 Progressive playback in the browser

**Decision.** The lesson page subscribes to `/api/generations/{id}/events`. It gets a snapshot with every
playable scene, then updates and new scenes; the stream ends after a final state, and the client stops
EventSource from reconnecting. Scenes are compiled in the browser by the same engine (P2). `buildLesson` takes
a cache, so already-compiled scenes are reused as new ones arrive.

The player keeps one clock for its whole life:
- **Pending scenes and chapters** show in the scene list and the scrubber. Seeking is limited to the ready part.
- **Buffering.** Reaching the end of what is ready holds the last frame under a "Preparing the next scene"
  overlay, and playback resumes by itself when the next scene arrives.
- **Render errors.** An error boundary holds the last good frame (spec Section 17).
- **Pacing.** The playhead is reported every 2 s for paced generation.

**Evidence.** In a headless browser against the scripted-LLM dev server (`scripts/dev_fake_llm.py`): the first
scene was playable after 3 calls, the overlay appeared when playback at 2x caught up, and playback resumed 5.7 s
later on the next scene.

---

## ADR-031 Live evaluation and model bake-off

**Decision.** `python -m animator.eval` (`make eval`) runs `config/eval_topics.json`: 16 topics across
physics, maths, biology, computer science and economics. It uses the live pipeline and a file store under
`var/eval/<run>/`, and writes `report.md` and `report.json` with these metrics:
- first-pass validity, ready within 3 corrections, corrections per scene, degraded rate;
- layout warnings per scene, time to first play, the worst generation-to-playback ratio;
- cost, and cost per minute of video.

A bake-off runs the set once per configuration with `--set ANIMATOR_MODEL_...=...` and compares the reports.

**Status.** Not run yet: the development key's account is deactivated (the harness stops cleanly on a rejected
key). The stage defaults stay as specified (Sol, with Astra for the final correction) until a bake-off justifies
changes. Record the result here.

---

## ADR-032 Cost-first model defaults (Luna, with Sol as the last-resort escalation)

**Measured.** The same 30-second Bernoulli lesson through the live pipeline, 2026-09-28. Prices are from the
OpenAI pricing page.

| Setup | Cost | Total time | First scene playable | Scenes right first time |
|---|---|---|---|---|
| gpt-6-sol, high effort | $0.28-0.58 | 6.6-8.4 min | ~3 min | 0-2 of 3 |
| gpt-6-luna, high effort | $0.043 | 8.4 min | 2.3 min | 0 of 3 |
| **gpt-6-luna, medium effort (planner low)** | **$0.019** | **3.7 min** | **1.4 min** | **2 of 3** |

**Where the cost goes.** A scene call sends about 49k input tokens: the scene schema (about 30k), the DSL
reference, the component catalog and exemplars. That input is cached after the first call. The model returns
about 4-6k output tokens, of which roughly half are hidden reasoning and about 30% of the visible JSON is null
filler required by strict mode. Output dominates: at Sol's $10/M it is about $0.05 per scene; at Luna's $0.50/M
it is about $0.003. Lower effort cuts the reasoning tokens, which also makes calls faster. gpt-4o costs more than
Sol for this workload ($1.25/M cached input, $10/M output) and is weaker at long structured output, so it is not
a candidate.

**Decision.**
- Every stage uses gpt-6-luna: planner at low effort, storyboard, scenes and corrections at medium.
- The final correction attempt escalates to gpt-6-sol (high). It is only used after three cheaper attempts
  failed.
- Fallbacks are cheap models (`gpt-5-mini`, `gpt-4.1-mini`).
- Prices were verified and added for all candidates.
- Best quality remains one setting away: set the stages to gpt-6-sol / high, for about 15x the cost.

**Trade-off.** Luna's scenes are correct and engaging but have more rough layout edges than Sol's (for
example a panel overlapping a diagram). The engine validators catch the serious ones (new rule `TITLE_COVERED`;
equations now count in the occlusion check), and the optional critic (`ANIMATOR_ENABLE_LLM_CRITIC`, cheap with
Luna) can polish further.

**Next levers, not yet taken.**
- Drop strict-mode null filler (about 30% of the visible output) by moving to non-strict JSON plus engine
  validation.
- Trim the input schema per scene.
- Use the Batch/Flex tiers (50% off) for lessons that are not watched live.

---

## ADR-033 Generation keeps up with playback (parallel scenes, streaming, patches, hedged drafts)

**Requirement.** A lesson must take at most as long to generate as it lasts: a 30-second lesson in 30 seconds
or less.

**Where the time went** (30-second lesson, gpt-6-luna at medium effort, ADR-032): 3.7 minutes. There were
three LLM stages in sequence (plan, storyboard, scenes). The scenes were generated one after another because
each scene continued from the previous scene's end state. Each scene took 40-60 s, and a correction rewrote the
whole scene (another 20-50 s). Measured on the live API, a call's time is almost all output: about 3-4 s before
the first token, then about 150 tokens/s on the standard tier and 225-255 tokens/s on the priority tier.

**Decision.** `ANIMATOR_SCENE_MODE=parallel` is the default.

1. *Scenes stand on their own.* Every scene starts on a clean stage: a fade, the default camera, and it
   creates everything it shows. The storyboard asks for consistent ids and looks across scenes. All scenes are
   generated at once (`ANIMATOR_SCENE_CONCURRENCY`). Long lessons storyboard all their chapters at once.
   `sequential` keeps the old carry-over behaviour.
   - The pipeline marks such scenes `transition_in.fresh`, a new DSL field that is left out of the model's schema
     (`x-llm-omit`). The engine then compiles a fresh scene without the previous scene's state, and its end
     state starts a new identity registry.
   - The player and the exporter therefore draw exactly what the pipeline validated. Without this, a lesson
     that reused an id for a different kind of object (`plant`: a group, then a path) failed in the browser's
     scene chain with `IDENTITY_TYPE_CHANGE`.
2. *One streamed outline call.* Lessons up to `ANIMATOR_OUTLINE_MAX_DURATION_S` (150 s) are planned and
   storyboarded in one call with a short plan (`outline.v2`). The reply streams in. A JSON scanner
   (`pipeline/streaming.py`) hands over each scene brief the moment it is complete, so scene 1 starts about 4 s
   in. Briefs that have already started are kept across retries.
3. *Low effort everywhere.* Reasoning tokens are pure latency here: every stage runs at low effort.
4. *Patch corrections.* A correction replies with only what changes: objects and actions by id, deletions,
   and the scene-level fields that change (`ScenePatch`, built from the engine's own scene schema, so it is
   just as strict). That is 150-1,000 tokens instead of 2,000-4,000, and 5-10 s instead of about 20 s.
   Actions timed after a deleted action inherit its start, so no reference dangles.
5. *Sure fixes without the model* (`pipeline/tidy.py`), each checked by the engine:
   - a start with both `at` and `after`/`with` keeps the relative one;
   - time references are written the engine's way (`b2` becomes `beat:b2`);
   - TeX aliases (`\longrightarrow` becomes `\rightarrow`);
   - an action ending a moment after the scene extends the scene;
   - an out-of-range number is clamped;
   - a bare object id where an anchor belongs becomes `<id>.center`;
   - an object is moved (and shrunk) as the engine's new `fix` hint says: `OUT_OF_BOUNDS`, `TITLE_COVERED`
     and `LABEL_OVERLAP` issues carry one. The move goes to the slot offset, the position, the pin offset, or a
     line's raw points. An object attached to one that also moves is left alone.

   The fixed scene is kept when it has fewer errors, or as many but none of the old ones: the engine checks
   layout only after timing and references are right. The model only sees what is left.
6. *Hedged drafts* (`ANIMATOR_SCENE_DRAFTS=2`, `ANIMATOR_CORRECTION_DRAFTS=2`). Each scene is drafted twice at
   once, and the first valid draft wins. When a draft comes back invalid, its correction starts at once, and the
   other draft keeps racing it. Corrections are also written twice at once. Losing calls finish in the
   background and their cost is counted (`Pipeline.settle`).
7. *Priority processing* (`OPENAI_SERVICE_TIER=priority`, "Fast mode"): about 1.6x the output speed for 2x the
   token price. `config/pricing.json` has per-tier multipliers, applied to the tier each response reports.
8. Three patch rounds (`ANIMATOR_MAX_CORRECTION_ATTEMPTS=3`), because each takes seconds. The Sol escalation
   is gone, since it took minutes.

**Measured** (live, 30-second lessons, 2026-09-29):

| Step | Total | First scene playable | Cost |
|---|---|---|---|
| Before (ADR-032) | 221 s | 84 s | $0.019 |
| Parallel scenes, one outline call, low effort | 67 s | 40 s | $0.011 |
| + streamed outline, patch corrections, sure fixes | 60-62 s | 28-30 s | $0.009 |
| + priority tier, short outline plan | 44 s | 14 s | $0.026 |
| + hedged drafts | 22-41 s (median about 27 s) | 12-24 s | about $0.03* |
| + engine fix hints, two patches per round (final build, 14 lessons) | 21-33 s (median about 26 s); 13 of 14 within 30 s | 14-25 s | $0.020-0.054, median $0.030* |

\* Costs include the drafts that lose the race: they finish, and are billed, a few seconds after the lesson is
ready. The first report of this table read costs at "ready" and missed them (about a third).
1-minute lessons cost $0.035-0.051; a 3-minute lesson cost $0.070.

1-minute lessons take 28-32 s (they were 5.2 minutes before). Every scene of the final-build lessons also played
in the browser's scene chain, and keyframes were checked by eye.

Found by the frame check and fixed:
- Unicode sub- and superscripts ("CO₂", "m²") have no glyph in the fonts and drew as "?". The text engine now
  draws them as `_{}`/`^{}` script runs.
- Text, labels and equations must be fully inside the frame (1% tolerance, was 10%). An equation card that
  clipped "oxygen" had passed.

**Trade-offs.**
- Scenes no longer carry objects over: each scene fades in and redraws the stage. The storyboard keeps ids and
  looks consistent.
- Cost per 30 s rises from about $0.009 to about $0.03: 2x for priority processing, and about 1.5x scene cost
  for hedged drafts.
- For the cheapest setup, set `OPENAI_SERVICE_TIER=` (empty) and `ANIMATOR_SCENE_DRAFTS=1`. Generation then
  takes about 2x the lesson length.
- Worst cases remain: a scene whose drafts both fail, and whose patches also miss, can take 35-45 s. A network
  outage pauses the lesson (resumable) after the retries.
- Low effort leaves some rough layout: a connector crossing a label, a pill overlapping a diagram. These are
  warnings, not errors. The optional critic (`ANIMATOR_ENABLE_LLM_CRITIC`) can polish them, at extra time.

---

## ADR-034 Cost review of the fast pipeline: no change (2026-09-29)

**Question.** Can LLM cost go down further without losing speed (ADR-033) or quality?

**Where the cost goes** (14 live 30-second lessons, including the drafts that lose the race; median $0.030):

| Part | Share of cost | Notes |
|---|---|---|
| Scene drafts | 83% | 6.5 per lesson (2 per scene, plus rare repairs); about 2,800 output tokens each, 8% of them reasoning |
| Patch corrections | 12% | about 2 per lesson; about 500 output tokens, 39% of them reasoning |
| Outline | 5% | one call, about 1,500 output tokens |

Input is almost all cached ($0.01/M), so output tokens are the bill. Two deliberate speed choices multiply it:
priority processing (2x the token price) and two drafts per scene (about 1.5x the draft cost).

**Options examined:**
- *A cheaper model for scene drafts.* Twelve real scene briefs were drafted once each, through the pipeline's own
  checks and sure fixes, on the priority tier:

  | Model | Valid | Time (median) | Cost/draft |
  |---|---|---|---|
  | gpt-6-luna, low (current) | 7/12 | 17.5 s | $0.0045 |
  | gpt-5-nano, minimal | 3/12 | 13.3 s | $0.0018 |
  | gpt-5-nano, low | 5/12 | 18.9 s | $0.0014 |
  | gpt-4.1-nano | 0/12 | 11.8 s | $0.0078 |

  gpt-5-nano is cheaper per draft, but it fails twice as often. Each failure costs a correction round (time) and
  risks a degraded scene, and its scenes are thinner (half the output). gpt-4.1-nano does not follow the scene
  format. gpt-4o-mini ($0.15/$0.60) and gpt-4o ($2.50/$10) cost more than Luna ($0.10/$0.50). Earlier runs showed
  gpt-5.4-nano/mini and gpt-4.1-mini making more errors. **Luna stays for every stage.**
- *Dropping rarely-set fields from the model's schema* (strict-mode `null` filler is 26% of a scene's characters).
  The candidate fields are in use: `rotation` 4.6% of objects (tilted leaves), `opacity` 7%, `decorative` 18%,
  `z` 7%, `parent` 2%. The saving would be at most about 5%, for visible losses. Not done.
- *Lower reasoning effort for patches.* The saving is about 4%. The risk is more failed patches (more rounds and
  time). Not done.
- *The second draft on the standard tier* (half price). This saves about 20%. But when the first draft fails
  (about 40% of scenes), the rescue comes about 5 s later. That would push about a quarter of 30-second lessons
  past 30 s. Not done by default, because the speed requirement comes first.
- *Cancelling the losing draft.* It is already about 90% written when the winner arrives, so this saves about 3%.
  Its cost would also have to be estimated. Not done.

**Decision.** No change: every remaining lever either saves little or costs speed or quality. The two cost knobs
remain in `.env` for anyone who wants cheaper and slower: `OPENAI_SERVICE_TIER=` (half the price, about 1.5x
slower) and `ANIMATOR_SCENE_DRAFTS=1` (about a third off, more correction rounds).

**Fixed alongside.** The lesson page showed the cost at the moment the lesson became ready. That missed the losing
drafts, which finish a few seconds later. The event stream now ends with a `final` event after the run, which
carries the full cost.

---

## ADR-035 Visual relevance: real icons, clean connections, and a layout polish (2026-09-29)

**Problem.** Lessons were understandable, but with rough edges that confuse weaker learners:
- people drawn as a rectangle with a circle on top, and a hand as a polygon;
- arrows cutting through boxes, labels and each other;
- labels straddling objects, content hidden under the caption bar, and emoji that drew as "?".

Measured on 116 recent scenes:
- a label straddled an object in 27% of scenes;
- content sat under the caption in 5%, and an emoji was undrawable in 7%;
- arrows through objects were not checked at all;
- a scene averaged 3 hand-built shapes and 0.4 icons.

**Decision.** The same model, cost and speed; the fixes come from the engine, the icon set and the prompt.

1. **80 new icons** from Lucide (ISC license; `scripts/vendor_icons.py` writes
   `packages/components/src/renderers/icons-lucide.ts` with the license text). They cover:
   - people and the body (person, people, person_standing, baby, hand, heart, brain, eye, ...);
   - nature, animals, science and lab objects;
   - technology (laptop, phone, server, database, robot, ...) and everyday things.

   The engine's `ICON_NAMES` lists them, and a test checks that every name has a drawing.
2. **New layout checks with sure fixes** (warnings; the scene still plays):
   - `CONNECTOR_CROSSES_OBJECT`: an arrow runs into another object and out of it. An arrow that ends at an object is
     not flagged.
   - `CONNECTORS_CROSS`: two arrows cross away from their ends.
   - For these and `CONNECTOR_CROSSES_LABEL`, the engine searches for the smallest new route. It tries the same ends
     bent (`curve`), or both ends on top, at the bottom or on one side, so an arc between two items in a row goes over
     or under its neighbours. The route must stay 10 px clear of everything it does not join.
   - `LABEL_OCCLUDES` gets a move hint when the label straddles the object's edge. A label written inside its box is
     left alone.
   - `UNDER_CAPTION` gets a lift. `NEAR_EDGE` gets a nudge inward.
   - `IssueFix` gains `params` (new values for `curve`, `from`, `to`).
3. **The polish pass** (`Pipeline._polish`, `ANIMATOR_LAYOUT_POLISH=true`). After a scene is valid, the hinted
   warnings are applied and the scene is compiled again:
   - moves go to a slot offset, position or pin offset; a label placed relative to another object changes side;
   - arrows get their new route;
   - characters the font cannot draw are removed;
   - the change is kept only when the scene stays valid and has fewer of these problems. At most two passes, no
     model call, well under a second.
4. **Prompt `animation.v4`**:
   - things that should look like themselves are icons (large, without the badge, for figures), never shapes;
   - arrows join facing sides, never pass through a third object or cross each other, and bend with `curve` when
     needed;
   - leave 32 px between objects; labels go beside what they name; no emoji.

**Measured.**

| | Before (v3, 116 scenes) | After (21 new scenes) |
|---|---|---|
| Scenes with a label straddling an object | 27% | 14% (the rest are labels written inside their box, which is intended) |
| Scenes with content under the caption / an undrawable emoji | 5% / 7% | 0% / 0% |
| Arrows through objects or across each other left | not checked | 1 in 21 scenes |
| Icons / hand-built shapes per scene | 0.4 / 3.0 | 1.7 / 1.4 |
| Draft validity on 12 real briefs | 7/12 | 10/12 |

- Speed is unchanged: 30-second lessons take 24-27 s, 1-minute lessons 24-32 s.
- Cost is unchanged: $0.023-0.034 per 30 s, $0.036-0.046 per minute.
- Keyframes of the same topics (laws of motion, MCP server, transformer) were compared by eye. Figures are now icons
  (hand, car, robot, person, heart), attention arcs go over the words instead of through them, and labels sit beside
  their objects.

**Rollback.** Git tag `backup/before-visual-relevance`, plus `.env.backup-before-visual-relevance`. Or partially:
`ANIMATOR_LAYOUT_POLISH=false` switches the polish off, and deleting `animation.v4.md` brings back prompt v3.

---

## ADR-036 Objects must not hide each other (2026-09-29)

**Problem.** In 58 of 139 recent scenes (42%), objects overlapped. Most of those overlaps were intended: wheels
touching a cart, a head on a body, a plant in its pot. But some hid content:
- two magnets drawn into each other, covering their N/S letters;
- a cart's load drawn over another cart's wheels;
- a wall drawn over a person.

The engine checked labels, titles and arrows, but never one object covering another. The reasoning-effort settings
are not the cause: no effort level makes the model check this, and higher efforts cost 2-4x the time.

**Decision.**
1. **Engine check `OBJECT_OVERLAP`** (a warning with a move hint).
   - It measures what is hidden: the share of the lower object (in drawing order) covered by an unrelated object
     drawn over it, flagged at 25% or more.
   - It ignores content drawn inside a shape (the upper object sits 90% or more within the lower one, like an icon
     in a circle), light touches (under 25% hidden), and small things on large regions (a shoe on the ground hides
     almost none of it).
   - It ignores parts of one drawing: grouped together, placed against or pinned to each other (compiled objects
     now record `placedOn`), or sharing the first word of their ids (`cart_body`, `cart_wheel`).
   - Effects, fluids, plots, containers, pipes, lines and open paths are not solid, so they are not checked.
2. **The polish moves the whole covering drawing.** Every part sharing the covering object's id prefix moves by
   the largest move any part needs, so a two-part magnet stays in one piece. Parts pinned to other objects follow
   those objects.
3. **Off-frame paths.** A path whose points are written in frame coordinates, but that is placed elsewhere too, is
   placed where its points say, instead of being pushed into a corner.
4. **Prompt `animation.v5`.**
   - Separate things never overlap at any moment, and a `move` ends where the object still has room: attracting
     magnets stop when they touch.
   - Parts of one drawing share the first word of their ids; a second copy of a drawing gets its own first word.

**Measured.**
- Existing scenes: the check flags 4 of 139. Three are real (magnets, a load over wheels, a wall over a person); one
  is an old-style composite (`cool_mug` / `mug_handle`) that the v5 naming rule prevents.
- The polish fixes the load and the wall. The magnets were animated into each other, so moving their start cannot
  help; v5 prevents it.
- New live lessons (magnets, laws of motion, Newton's third law, bicycle): 0 overlaps in 16 scenes. In the new
  magnets lesson, the magnets snap together edge to edge.
- Speed and cost are unchanged. The first lesson after a prompt change pays once for the uncached prompt (about
  $0.02).

**Rollback.** Git tag `backup/before-overlap-and-tts`. Deleting `animation.v5.md` returns the prompt to v4.
`ANIMATOR_LAYOUT_POLISH=false` disables all polish moves.

## ADR-037 Narration voice: swappable text to speech behind one switch (2026-09-30)

**Problem.** Lessons had captions only. The caption highlights words as if someone were reading aloud, but no
one spoke. The voice has to follow the captions word by word, with natural pauses at punctuation. It must cost
nothing to run, keep up with generation, and be removable with a single setting.

**Decision.**
1. **One switch, `ANIMATOR_TTS_ENABLED` (default off in code).** Off means:
   - no provider is built;
   - the pipeline, API, player and export run exactly as before;
   - the API also withholds narration of lessons made earlier with a voice.
2. **A provider interface** (`animator/tts`):
   - `synthesize(text) -> Speech(audio, format, duration, word marks | None)`;
   - providers: `edge` (default: free, no key, exact word timings), `azure`, `google`, `elevenlabs`, `openai`
     (also any local OpenAI-compatible server, such as Kokoro-FastAPI), and `fake` for tests;
   - keys live in `.env` and are sent only to their provider; errors never contain them;
   - a missing key turns narration off with a logged reason (`/api/config` shows it); it never stops the API.
3. **Speech is prepared while the scene is written.** The storyboard's sentences are synthesized as soon as the
   scene starts. They are cached by content (provider, voice, speed, text), and concurrent requests share one
   synthesis.
4. **Beats stretch to fit the speech, never shrink.** This is a DSL transform in Python (`pipeline/narration.py`),
   like the other deterministic fixes:
   - numeric times inside a stretched beat move proportionally, and later times shift;
   - beat references, chains, delays and motion durations are unchanged, so every action still ends inside the
     scene;
   - the engine validates the result, and the original timing is kept if that fails.
   docs/TTS.md had planned this as an engine `retime` exposed by the worker. The Python transform needs no
   protocol change, and the engine still has the last word through validation.
5. **Word timings are computed once, on the server.** Provider marks are matched to the caption's words (the
   engine's word splitting). Unmatched words are estimated from length and punctuation, inside the real audio
   duration. The caption uses these times when their count matches its words, and its old timing otherwise.
6. **Playback follows the player clock** (pause, seek, speed, checkpoints, buffering), re-syncing when the audio
   drifts by more than 0.25 s. **Export** places each beat's audio at its time and muxes it as AAC; the captions
   in the video follow the voice. Segment cache keys include the narration.
7. **Speech never fails a lesson.** On any error the scene keeps captions only, and `tts_failures` counts it.

**Measured (edge, voice en-US-AvaMultilingualNeural, live).**
- 30-second lesson:
  - ready in 25.4 s, first scene playable at 21.4 s;
  - $0.048 including one correction and the losing drafts, inside the usual $0.020-0.054; speech adds no model
    call and is free;
  - the lesson grew from 30 s to 32.6 s, because one beat's sentence was longer than the model's timing.
- 1-minute lesson: ready in 29.1 s, first playable at 13.7 s, $0.045 (usual: $0.035-0.051).
- Every beat voiced, with every word timed by the provider (none estimated).
- The voice step adds 0.02-0.07 s per scene, because the prefetch had the speech ready.
- Export: an AAC track exactly as long as the video. In a checked frame, the caption highlights "imaginary" at the
  moment the voice says it.
- edge-tts latency varies (1-9 s for 16 sentences at once), which the prefetch hides.

**Rollback.** `ANIMATOR_TTS_ENABLED=0`, or git tag `backup/before-overlap-and-tts` together with
`.env.backup-before-tts`.

## ADR-038 Local Kokoro voice, a voice per lesson language, and Hinglish lessons (2026-09-30)

**Problem.**
- The user started Kokoro-FastAPI and switched `.env` to it, but lessons stayed silent. `ANIMATOR_TTS_BASE_URL`
  was written twice; the later, empty line won. Every sentence went to OpenAI with the model name `kokoro`
  (HTTP 404), and every scene fell back to captions only. That fallback is by design, but nothing on screen
  said so.
- The user also wanted Kokoro's best voice as the default, a way to hear voices before choosing, and lessons in
  Hinglish as well as English.

**Decision.**
1. **`kokoro` provider.** It uses Kokoro-FastAPI's `/dev/captioned_speech`, which returns the time of every word,
   so captions follow the voice exactly. The OpenAI route only gave estimated timings.
   - Defaults: `af_heart` (Kokoro's only A-grade voice) for English and `hm_omega` for Hinglish.
   - The OpenAI-style `/v1` URL is accepted too.
2. **Failures are visible.**
   - A lesson records why its voice failed (`tts_error`), and the lesson page shows "Voice: N scene(s) have
     captions only" with the reason.
   - The home page shows a configuration problem, and any narration setting written twice in `.env` (also
     logged).
   - The `openai` provider refuses model `kokoro` without a base URL, with a message that names the fix.
3. **A voice per lesson language.**
   - `synthesize(text, voice)`: the speech cache keys by voice, and the pipeline asks for the voice of the
     lesson's language.
   - `ANIMATOR_TTS_VOICE_HINGLISH`, empty by default, picks each provider's Hinglish default: kokoro `hm_omega`,
     edge/azure `en-IN-NeerjaExpressiveNeural`, google `en-IN-Neural2-A`, and elevenlabs/openai their English
     voice.
4. **Hinglish lessons.**
   - A Language choice on the create form: English or Hinglish.
   - English prompts are byte-for-byte unchanged (`Language: English`), so English lessons and prompt caching
     are unaffected.
   - For Hinglish, the language line spells out the everyday Hindi-English mix in Roman letters, with technical
     terms in English. It forbids Devanagari, which the fonts cannot draw.
5. **Choosing Hinglish voices.** The same two Hinglish sentences were spoken in 11 candidate voices and
   transcribed back with gpt-4o-transcribe (about $0.01).
   - All six Microsoft voices and Kokoro `hm_omega` were perfect on both.
   - Kokoro `hf_beta` slipped once; `hf_alpha` and `hm_psi` more often.
   - Kokoro's American `af_heart` garbled the Hindi words.
6. **Kokoro's speed on this laptop** (i7-1265U, no NVIDIA GPU):
   - about real time (5.1 s for 5.4 s of speech), and parallel requests are no faster;
   - `ANIMATOR_TTS_CONCURRENCY=1` therefore synthesizes in scene order, so the first scene's voice is ready
     first and the rest stays ahead of playback.
7. **Listening before choosing.** `scripts/tts_samples.py` writes a page with an English and a Hinglish sample for
   each recommended voice of the configured provider. Kokoro's own page, http://localhost:8880/web/, also plays
   any text in any voice.
8. **Repository.** The user's Kokoro-FastAPI checkout inside the project is ignored by git, Biome and Ruff.

**Measured (live, Kokoro on the CPU).**
- English 30-second lesson:
  - ready in 48.1 s, first scene playable at 25.3 s;
  - the total was set by one scene's three layout corrections (38 s of model time);
  - the voice added 0-2.3 s per scene.
- Hinglish 30-second lesson:
  - ready in 36.8 s, first scene playable at 13.7 s, $0.02;
  - every scene voiced by `hm_omega`, with exact word timings;
  - titles and captions in Roman-script Hinglish, no Devanagari anywhere.
- In the player, the voice plays in step with the highlighted words, with no errors.

**Rollback.**
- `ANIMATOR_TTS_PROVIDER=edge`: the fastest provider; Hinglish lessons then use Neerja.
- `ANIMATOR_TTS_ENABLED=0`: no voice anywhere.
- Git tag `backup/before-kokoro-hinglish` with `.env.backup-before-kokoro-hinglish`.

## ADR-039 The lesson language is the spoken language; Hinglish speaks with an Indian female voice (2026-09-30)

**Problem.**
- Hinglish lessons (ADR-038) also wrote their titles, labels and quiz in Hinglish. The user meant only the spoken
  explanation; everything drawn stays English.
- The Hinglish voice (Kokoro `hm_omega`) sounded male and English-accented. The user needs a female voice with an
  Indian (Hindi) accent.

**Decision.**
1. **The language line tells the model that Hinglish is for the narration only** (the voice and its captions).
   Everything else is English, exactly as in an English lesson: lesson and scene titles, every text on screen, the
   descriptions of what happens on screen, and checkpoint questions, choices and explanations.
   - English lessons are unaffected (byte-identical prompts).
   - No other prompt, schema, engine or DSL change.
2. **Captions stay in the spoken language.** English captions over a Hinglish voice were considered. They would
   need a second, mapped version of every sentence, so more output tokens and time on every scene. And word-by-word
   highlighting cannot work across two languages, whose words and word order differ: only sentence-level sync
   would be possible. The user chose Hinglish captions with the Hinglish voice.
3. **Kokoro cannot give an Indian accent here.** Its phonemizer turns Roman-script Hindi into English phonemes,
   even in the Hindi voices: "use neeche ... hai" becomes `juːs nˈiːʧ ... hˈI`. The same words in Devanagari get
   Hindi phonemes (`ˈʊseː nˈiːceː ... hɛː`). Captions must stay in Roman letters (the fonts have no Devanagari).
4. **A provider per lesson language.**
   - `ANIMATOR_TTS_PROVIDER_HINGLISH` routes Hinglish lessons to another provider (the user's `.env`: edge),
     while English stays on Kokoro.
   - Each provider has its own queue: a CPU Kokoro at concurrency 1 never holds up edge.
   - A Hinglish provider that cannot be built (a missing key) leaves Hinglish on the main provider, with a warning.
5. **Default Hinglish voice for edge/azure: `hi-IN-SwaraNeural`**, a native Hindi female voice.
   - An audio model (gpt-audio-1.5) listened to the same Hinglish lines in each female candidate. Swara scored
     highest: Hindi 9-10, English terms 8-9, naturalness 9. The Neerja voices came next.
   - The judge heard Kokoro's `hf_beta` as male in both trials.
   - The judge's accent labels were not discriminating (it called every voice "native Hindi"), so the phoneme
     evidence above decides against Kokoro.

**Measured (live).**
- Hinglish 1-minute lesson:
  - ready in 25.1 s, first scene playable at 15.6 s, $0.034;
  - Swara on every scene, with exact word timings;
  - Hinglish captions; every title, label, card and quiz in English.
- English 30-second lesson: unchanged (Kokoro `af_heart`, English captions and visuals), $0.029.

**Rollback.** Git tag `backup/before-hinglish-voice-fix` with `.env.backup-before-hinglish-voice-fix`.

## ADR-040 Visual glitches: geometric checks, measured fixes, and one repair round (2026-09-30)

**Problem.** A review of 50 recent scenes found glitches a viewer notices:
- a row of numbers wrapping onto two lines, so boxes and pointers aimed at the wrong items;
- a card, a ramp or a magnifier drawn over text;
- a beaker drawn into a tank, and a block sunk through a tank's floor;
- text 24 px from the frame edge, and a shape running behind the caption bar;
- arrows that ended up in the frame's corner;
- a ring cutting through the row it circled.

Most of these were not detected at all. A valid scene's warnings were never sent back to the model, so anything
the deterministic polish could not fix stayed on screen.

**Research.**
- Studies of LLM-made teaching videos report the same problems: layout is the weakest part (Code2Video,
  TheoremExplainAgent).
- SGA ("Plug & Play Geometric Verification for Educational Video Synthesis", 2026) found that exact geometric
  checks with a measured minimum move beat vision-model critics. People preferred its videos in 84% of
  comparisons, and 93% on overlaps. It also checks positions at rest, not in the middle of a motion.
- Our engine already follows that design, so this ADR fills in its gaps.

**Decision.**
1. **Engine bugs.**
   - Objects drawn from their endpoints (arrows, lines) rotate and scale about their own center; they used to
     swing around the frame's corner.
   - A `circle` marker draws a ring that clears its target's corners: an ellipse through points beyond the
     corners, or a capsule around a long row.
2. **New checks** (warnings, most with a measured fix):
   - `HIDDEN_TEXT`: text, or an arrow's label, covered by something drawn after it. The fix moves the lighter
     of the two.
   - `OUT_OF_STAGE`: content within 40 px of a side edge (the fix moves it to 48 px), more than 24 px into the
     caption zone, or deep in the title bar.
   - `TEXT_WRAPPED`: a row of short items that wraps. The fix sets the width that keeps it on one line;
     otherwise the message asks for at most about 16 items with "…".
   - `LEAVES_CONTAINER`: something that was inside a container, or sticks out through its floor or walls, at
     rest.
   - Two containers overlapping by more than 35% (a catch cup under a spout overlaps about 27%, on purpose).
   - A plain line (a wire) running through a label's text.
3. **Calibrated against the approved fixture lessons.** They stay free of warnings. The one real finding there,
   a pendulum crammed into the gallery's corner, was moved 50 px; the only visual change is that one frame.
4. **False alarms removed on real scenes.**
   - Touches (a sperm reaching an egg) are not glitches.
   - Letters written on a magnet's pole are not glitches.
   - A see-through region under an object is not an overlap.
5. **One repair round (`ANIMATOR_GLITCH_REPAIR`, on).** *(Reverted by ADR-042.)*
   - A valid scene that still shows a glitch after the polish gets one patch round. The message lists the
     glitches with the engine's measured moves, "move it by about (-24, 0) px" (SGA's surgical patching).
   - The result is kept only if it stays valid and shows fewer glitches.
   - Only affected scenes pay: $0.005-0.02 and 7-20 s.
   - `glitch_repairs` and `glitches_left` are recorded per lesson.
6. **Prompt `animation.v6`** *(reverted by ADR-042)*: v5 plus the no-glitch rules (text never covered, margins, rows of at most about
   16 items, contents stay in their container, shapes stand on the ground, arrows are not rotated).

**Measured.**
- The 50 reviewed scenes, as the model wrote them, then after the polish, then after one repair of the 6
  still glitched:

  | | Glitch-free |
  |---|---|
  | As written | 35 of 50 |
  | After the polish | 44 |
  | After one repair | 49 |

- Rendered before and after:
  - the number rows sit on one line, circled cleanly;
  - the beaker stands beside the tank, and the blocks stay in their tanks;
  - the recap card no longer covers the arrow.
- Twelve new live lessons (v6, checks, repair; voice off):
  - 30 s-1 min lessons, ready in 24-38 s;
  - first scene playable in 14-36 s (a scene that needs a correction or a repair first pays 7-20 s);
  - $0.020-0.060 per lesson;
  - no simplified scenes;
  - the second round had 1 glitch left in 20 scenes.

**Model research.**
- There is no GPT-6.1 Luna. The only 6.1 model is **GPT-6.1 Sol** (DevDay, 29 Sep 2026): $2 input,
  $0.10 cached, $10 output per 1M tokens, 20x Luna.
- On the same 17 scene briefs:

  | | GPT-6 Luna (low) | GPT-6.1 Sol (low) |
  |---|---|---|
  | Valid first drafts | 15 | 14 |
  | Glitch-free after the polish | 9 | 12 |
  | Time per scene (median) | 14 s | 32 s |
  | Cost per scene | $0.0034 | $0.073 |

- Luna with the repair round gets about as clean, faster and at a fraction of the cost, so Luna stays.
- `gpt-6.1-sol` is added to `config/pricing.json` and `config/models.json` for anyone who wants to try it.

**Rollback.**
- `ANIMATOR_GLITCH_REPAIR=false` turns off the repair round.
- `ANIMATOR_LAYOUT_POLISH=false` turns off the polish.
- Deleting `animation.v6.md` returns the prompt to v5.
- Git tag `backup/before-visual-glitch-fixes` with `.env.backup-before-visual-glitch-fixes`.

---

## ADR-041 The teacher's voice: no dead air, a speech script, spoken punctuation (2026-09-30)

**Problem** (heard with ElevenLabs):
- A 1-2 s cut between sentences within a scene.
- A flat, robotic delivery: questions not sounding like questions, no pauses at commas.
- Hinglish that did not sound like an Indian teacher.
- Roman Hindi read with English spelling rules: "sunati" said as "sunti", "ghumati" as "ghumti".

**Causes.**
- **The cut.** A beat lasted until its audio file ended, plus a 0.35 s pause. Voices put up to 0.9 s of
  silence after the last word (measured on edge: 0.88 s), so about 1.3 s was heard. Beats were also only ever
  lengthened, so the model's generous beat times stayed as silence after short sentences.
- **Pronunciation.** Roman Hindi is ambiguous (a/aa, i/ee), and voices resolve it with English rules. Tested
  on edge's `hi-IN-SwaraNeural`: "sunati" became सुनती and "ghumati" became घूमती. The same words written in
  Devanagari were said right.
- **Punctuation.** `:` and `;` were read as full stops.
- **Each sentence was synthesized alone**, so it started with fresh intonation, and the voice had no idea of
  its tone.
- **How the Hinglish is written.** The outline and storyboard calls write it directly, in Roman letters; it is
  not translated from English. They were told only "Hinglish, Roman letters, English terms".

**Decision.** All changes are in the voice layer and the Hinglish language line. English lessons receive the
same model instructions as before. The visuals and the scene prompt are unchanged.
1. **Beats fitted to the speech** (`ANIMATOR_TTS_FIT_BEATS`, on).
   - A beat lasts until its last word ends, plus `ANIMATOR_TTS_PAUSE_S` (0.35 s; 0.6 s after a scene's last
     sentence). Long silences are shortened, but not below what the beat's own actions need (an action at
     `beat:b2` with a 2 s delay keeps 2.3 s).
   - Three timings are tried in order: fitted, halfway, stretch-only. The first that the engine finds valid
     with no more visual glitches than before is kept; otherwise the model's timing stays.
   - The halfway try exists because action durations are not scaled. A fully fitted scene can move a label
     across an object that is mid-motion (seen once in 13 scenes: `LABEL_OCCLUDES` at 5 s); halfway kept it clean.
2. **The speech script** (`ANIMATOR_TTS_SCRIPT=hinglish`; `tts/script.py`, prompt `speech_script.v1.md`).
   - One `lightweight` call per scene (Luna, low) rewrites each caption for the voice: Hindi in Devanagari,
     English terms as they are, spoken punctuation, and a tone.
   - It must return exactly one entry per caption word, so the voice's word times map back onto the caption
     words and the captions keep lighting up word by word. The captions themselves never change.
   - It runs while the scene is drawn (with the speech prefetch) and is cached in `var/tts/scripts`.
   - A failed call, or a reply that does not line up, speaks the caption as written.
3. **Spoken punctuation without a script**: `:` and `;` are read as commas (not `10:30`).
4. **ElevenLabs delivery.**
   - Each request carries the previous and next sentence (`previous_text`/`next_text`), so the intonation
     flows.
   - `ANIMATOR_TTS_STABILITY` 0.4 and `ANIMATOR_TTS_STYLE` 0.3 by default (more expressive than the API
     defaults).
   - `eleven_v3` gets the tone as an audio tag (`[curious]`, `[encouraging]`, ...). The tag is not a caption
     word.
5. **The Hinglish language line** *(reverted by ADR-042)* now describes a warm Indian teacher: short sentences; dekho, socho, chalo,
   achha, yaani; questions to the learner; praise; tum or aap; a comma at every pause, `?` and `!`, never `;`
   or `:`; the common Roman spellings. Everything on screen stays English (ADR-039).

**Measured** (live, ElevenLabs `eleven_flash_v2_5`, two 30-second Hinglish lessons and one English):
- Silence between sentences: 0.35 s in the Hinglish lessons; the English lesson averaged 0.59 s. Before, it was
  about 1.3 s. The English lesson's one 1.8 s gap was in the scene whose fitting added a glitch; the halfway try
  now brings it to about 1.1 s.
- The speech script: 3 calls, $0.001 per lesson, 2-4 s each, in parallel with the scene calls, so no added
  wait. Devanagari came back for every Hindi word (देखो, सोचो, बिल्कुल सही), the English terms stayed English,
  and the tones matched (questions curious, praise excited or warm).
- Lessons were ready in 30-34 s, first scene playable in 19-22 s, scenes valid, 0 glitches left.
- ElevenLabs: about 0.5 credit per character on flash (574 characters for a 30-second lesson).

**Scene effort (asked with this ADR: would Luna at its maximum effort place objects better?).** Luna's top
effort is `high`; `max` and `xhigh` map to it (`config/models.json`). First drafts of the same 17 scene briefs
as ADR-040, through the pipeline's own checks and polish:

| Luna effort | Valid first drafts | Glitch-free | Time per scene (median / max) | Cost per scene |
|---|---|---|---|---|
| low (current) | 12 | 8 | 15 s / 27 s | $0.0037 |
| medium | 15 | 13 | 32 s / 61 s | $0.0076 |
| high (= max) | 11 | 10 | 61 s / 107 s | $0.0110 |

- High is not better than medium. It made more reference and schema errors (unknown objects and actions,
  invalid fields), and it is 4x slower. ADR-032 saw the same on whole lessons.
- Medium drafts are cleaner, but they take twice as long. Scenes are drawn in parallel, so a 30-second lesson
  would take about 45-60 s instead of about 30 s, against the speed rule (generation time no longer than the
  lesson). At low, the corrections and the glitch repair round catch most of the difference: the live lessons
  above had 0 glitches left.
- Low stays. `ANIMATOR_EFFORT_ANIMATION_PLANNER=medium` is the one setting to change for cleaner first drafts
  at the cost of speed. The sample is small: the same briefs at low gave 15 valid drafts in ADR-040 and 12 here.

**Rollback.**
- `ANIMATOR_TTS_SCRIPT=off` reads the captions as written.
- `ANIMATOR_TTS_FIT_BEATS=0` only stretches beats, as before.
- Git tag `backup/before-tts-teacher-voice` with `.env.backup-before-tts-teacher-voice`.

---

## ADR-042 Model-side changes of ADR-040 and ADR-041 reverted (2026-09-30)

**Why.** The user found the scenes worse after ADR-040 and ADR-041: poorly placed objects that do not relate to
each other, and a scene replaced by the "Simplified scene" card showing its description. The user asked for every
change on the model side to be reverted, without new fixes.

**What was on the model side.**
- No model was switched. Every stage still uses gpt-6-luna at the same efforts; the effort benchmark in ADR-041 ran
  outside the app.
- ADR-040: the scene prompt `animation.v6`, and the model repair round for visual glitches (the `glitches` message
  in `stages.py`, `Pipeline._repair_glitches`, `ANIMATOR_GLITCH_REPAIR`).
- ADR-041: the longer Hinglish language line. It is sent to every stage of a Hinglish lesson, the scene stage
  included.

**Decision.** All three are reverted to their state before ADR-040:
- `animation.v6.md` is deleted, so scenes use `animation.v5` again;
- `stages.py` is restored as it was at ADR-039;
- the repair round and its setting are removed;
- `language.py` is restored as it was at ADR-040.

Kept (not model-side):
- the engine's glitch checks and the deterministic polish (ADR-040);
- the voice layer (ADR-041), including its own speech-script call, which only rewrites what the voice reads;
- the `glitches_left` count per lesson.
The inert `gpt-6.1-sol` entries in `config/models.json` and `config/pricing.json` stay; no stage uses them.

**What was seen.** In the two reported lessons (Hinglish, 1 minute):
- One scene failed four times: a keyframe color written as text, overlapping labels, then parents that did not
  exist. The pipeline then showed its fallback card. That is the model failing to write a valid scene.
- No literal `
` was found in any saved scene of the last 40 lessons. The one line break in a scene was a real
  one ("North: summer / South: winter").

**Rollback.** Git tag `backup/before-scene-voice-and-llm-revert` with
`.env.backup-before-scene-voice-and-llm-revert`.

---

## ADR-043 One take per scene: the voice reads the whole scene in one go (2026-10-01)

**Problem.** After ADR-041 the silences were gone, but each sentence was still its own recording. At every
sentence the voice ended, then started again with fresh intonation, which sounded like a cut. The user asked for
each scene's explanation to be spoken at once, with only the natural pauses of commas, full stops and
exclamations, and a pause of about 1 s between scenes.

**Decision** (`ANIMATOR_TTS_ONE_TAKE`, on; the voice layer only, no model change):
- **One recording per scene.** The scene's sentences are joined and sent to the voice as one request; with a
  speech script, the sentences' scripts are joined. The voice reads the whole explanation in one go.
- **Each beat plays its part.** The recording's word times give each sentence's first and last word. Two sentences
  are cut in the middle of the pause the voice made between them, so each beat's part holds exactly its own
  words, and its captions light up word by word as before.
- **Fitting.**
  - Each beat lasts exactly its part, so the next sentence starts as the previous part ends. The player keeps
    playing the same audio without a restart.
  - The last beat lasts until its last word, plus `ANIMATOR_TTS_SCENE_PAUSE_S` (1.0 s) of quiet before the next
    scene.
  - The same validation as ADR-041 applies: fitted, then halfway, then stretch-only. The first timing the engine
    accepts without a new visual glitch is kept.
- **Narration record.** Every beat of a scene names the same audio, with an `offset` into it and the `duration`
  it plays.
  - The player plays from `offset`; a missing offset means 0, so older lessons play as before.
  - The export decodes each recording once and places each beat's slice at its time.
- **Fewer requests:** one per scene instead of one per sentence. A sentence the scene changed after the
  prefetch means the whole scene is synthesized again (a few hundred characters).

**Measured** (live, ElevenLabs `eleven_flash_v2_5`; a 1-minute Hinglish lesson and a 30-second English one):
- One recording per scene, 8 of 8 scenes. Every scene's beats are back to back (offsets and starts contiguous),
  so the audio never restarts within a scene.
- Between sentences: only the voice's own pause at the full stop, 0.0-0.95 s (0.4 s on average).
- Between scenes: 1.0-1.1 s of quiet.
- An exported MP4 has the voice during the speech and true silence in the pause after each scene's last word.
- Speech adds no wait. One lesson's first scene was ready only after 52 s, because the model failed that scene
  four times (`GROUP_PARENTED`) and it was replaced by the simplified scene. That is scene drawing, unchanged
  here.

**Rollback.**
- `ANIMATOR_TTS_ONE_TAKE=0` speaks sentence by sentence (ADR-041).
- Git tag `backup/before-scene-voice-and-llm-revert` with `.env.backup-before-scene-voice-and-llm-revert`.
