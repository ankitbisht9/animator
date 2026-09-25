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
