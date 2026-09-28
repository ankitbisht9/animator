# Prompt packs

The LLM pipeline has four prompt packs, versioned as files in
`apps/api/src/animator/pipeline/prompts/`. Each call records its prompt version in the generation's call log.
To change a prompt, add a new version file (`name.v2.md`) and point `load_prompt` at it. Don't edit a version that
recordings depend on: replay fixtures are keyed by the full prompt text.

| Stage | File | Output (strict JSON schema) | Model setting |
|---|---|---|---|
| 1. Educational planner | `planner.v1.md` | `EducationalPlan` (chapters of 45-90 s) | `ANIMATOR_MODEL_EDUCATIONAL_PLANNER` |
| 2. Storyboard (per chapter) | `storyboard.v1.md` | `StoryboardChapter` (scenes, beats, identity, engagement) | `ANIMATOR_MODEL_STORYBOARD` |
| 3. Animation planner | `animation.v1.md` + DSL reference + component catalog + exemplars | the engine's LLM-facing Scene schema | `ANIMATOR_MODEL_ANIMATION_PLANNER` |
| Correction | same instructions as stage 3 | same schema | `ANIMATOR_MODEL_CORRECTION`, final attempt `..._CORRECTION_FINAL` |
| Critic (optional) | `critic.v1.md` | `CriticReport` (pass/revise, issues with fixes) | `ANIMATOR_MODEL_CRITIC` / `..._VISUAL_CRITIC` |

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
- Scene answers are validated by the engine worker (schema, semantics, layout). Errors go back to the model as
  `[error] CODE at /path: message -> suggestion` lines. After the last correction attempt, a clearly flagged
  simplified scene is shown instead.
