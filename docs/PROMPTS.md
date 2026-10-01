# Prompt packs

The LLM pipelines' prompt packs are versioned as files in
`apps/api/src/animator/pipeline/prompts/`. Each call records its prompt version in the generation's call log.
To change a prompt, add a new version file (`name.v2.md`) and point `load_prompt` at it. Don't edit a version that
recordings depend on: replay fixtures are keyed by the full prompt text.

`load_prompt` picks the newest version of each pack, except where a pipeline pins one. There are two
pipelines (`ANIMATOR_PIPELINE`).

### The teaching pipeline (the default, ADR-044)

| Stage | File | Output (strict JSON schema) | Model setting |
|---|---|---|---|
| Teacher | `teacher.v1.md` | `TeachingScript` (the whole explanation: every spoken sentence and what is seen, with the core idea, intuition, misconception and worked example) | `ANIMATOR_MODEL_TEACHER` (medium) |
| Reviewer | `review.v1.md` | `ScriptReview` (what was fixed, and the improved script) | `ANIMATOR_MODEL_REVIEWER` (low) |
| Visual director | `director.v1.md` | `Direction` (the lesson's look, then each scene's composition and what each beat shows), streamed | `ANIMATOR_MODEL_DIRECTOR` (medium) |
| Scenes | `animation.v7.md` + DSL reference + component catalog + exemplars | the engine's LLM-facing Scene schema | `ANIMATOR_MODEL_ANIMATION_PLANNER`, at `ANIMATOR_EFFORT_TEACHING_SCENE` (medium) |
| Correction | same instructions as the scenes | `ScenePatch` | `ANIMATOR_MODEL_CORRECTION`, final attempt `..._CORRECTION_FINAL` |
| Visual critic | `critic.v2.md` | `CriticReport` | `ANIMATOR_MODEL_VISUAL_CRITIC` |

- The teacher's message gives the length to aim for: duration x 2.5 words per second, split over
  `max(3, duration / 14)` scenes.
- The reviewer's message gives the script's measured word count against that target. Models cannot count words.
- The director gets the reviewed script. Each scene brief takes the script's narration word for word and the
  director's plan for the picture.
- The scene message adds the lesson's look and the whole lesson's narration, so each scene knows where it sits.
- The critic's summary adds the engine's measured composition at each keyframe.

### The fast pipeline (ADR-033)

It uses the scene and critic versions it was tuned with, so `ANIMATOR_PIPELINE=fast` behaves as before ADR-044.

| Stage | File | Output (strict JSON schema) | Model setting |
|---|---|---|---|
| 1+2. Outline (lessons up to 150 s) | `outline.v2.md` | `LessonOutline` (a short plan, the scenes, a recap), streamed | `ANIMATOR_MODEL_STORYBOARD` |
| 1. Educational planner (longer lessons) | `planner.v2.md` | `EducationalPlan` (chapters of 45-90 s) | `ANIMATOR_MODEL_EDUCATIONAL_PLANNER` |
| 2. Storyboard (per chapter) | `storyboard.v2.md` | `StoryboardChapter` (scenes, beats, identity, engagement) | `ANIMATOR_MODEL_STORYBOARD` |
| 3. Animation planner | `animation.v5.md` (pinned) + DSL reference + component catalog + exemplars | the engine's LLM-facing Scene schema | `ANIMATOR_MODEL_ANIMATION_PLANNER` |
| Correction | same instructions as stage 3 | `ScenePatch` (only what changes; built from the Scene schema) | `ANIMATOR_MODEL_CORRECTION`, final attempt `..._CORRECTION_FINAL` |
| Critic (optional) | `critic.v1.md` (pinned) | `CriticReport` (pass/revise, issues with fixes) | `ANIMATOR_MODEL_CRITIC` / `..._VISUAL_CRITIC` |

The outline and storyboard packs have a `{continuity}` slot, filled from `continuity-parallel.v1.md` or
`continuity-sequential.v1.md` depending on `ANIMATOR_SCENE_MODE`. In parallel mode (the default), every scene
starts on a clean stage (ADR-033).

## Assembly (static first, dynamic last)

- **`instructions`: static.** The rules, then for stage 3 the generated `docs/DSL.md` and `docs/COMPONENTS.md`, then
  three hand-authored Archimedes scenes as exemplars (a character scene, a solver and binding scene, an
  equation scene). The layouts, slots and solver outputs come from the engine worker's `catalog.get`. These are
  identical for every scene of every lesson, so the provider can cache them. The correction stage reuses stage 3's
  instructions byte for byte, so corrections hit the same cache.
- **User message: dynamic.**
  - Planner: the request.
  - Storyboard: the plan, the chapter, earlier scenes and the objects on stage.
  - Animation planner: the scene brief, the previous scene's engine-computed state summary, and the object ids
    used so far.
  - Correction: additionally the previous version and the engine's structured issues, or the reason the previous
    reply was unusable.
  - Critic: the brief, a summary of the compiled scene and, for the visual critic, rendered keyframes.

## Teaching style built into the prompts

The packs ask for lessons that work for every learner, including ones who struggle:
- one idea at a time, a picture for every idea, everyday words, and terms defined on first use;
- a hook, predictions and quick checks, with misconceptions named and corrected;
- engagement devices in every scene: faces on bodies, speech bubbles, badges, bursts on "aha" moments, live
  readouts;
- numbers never typed by the model: every number on screen is bound to a solver or computed value, which the engine
  calculates (P3).

## Output handling

- Plan and storyboard answers are validated with Pydantic. A refused, truncated or invalid answer is shown back to
  the model and retried up to three times. If a chapter's storyboard still fails, that chapter becomes one scene
  outlined from the plan.
- Scene answers are validated by the engine worker (schema, semantics, layout).
  - Slips with one obvious reading are fixed first, without the model (`pipeline/tidy.py`). These include an
    action ending a moment late, TeX aliases, and an off-frame object the engine says how to bring back.
  - Valid scenes are polished the same way: labels moved off objects, arrows rerouted around what they ran
    through, nothing under the caption, and undrawable characters removed (ADR-035).
  - The remaining errors go back to the model as `[error] CODE at /path: message -> suggestion` lines. The
    model answers with a patch.
  - After the last correction attempt, a clearly flagged simplified scene is shown instead.
