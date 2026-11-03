# Prompt packs

The LLM pipelines' prompt packs are versioned as files in
`apps/api/src/animator/pipeline/prompts/`. Each call records its prompt version in the generation's call log.
To change a prompt, add a new version file (`name.v2.md`) and point `load_prompt` at it. Don't edit a version that
recordings depend on: replay fixtures are keyed by the full prompt text.

`load_prompt` picks the newest version of each pack, except where a pipeline pins one. There are two
pipelines (`ANIMATOR_PIPELINE`).

### The teaching pipeline (the default, ADR-044; lessons that fit their length, ADR-063; lessons that flow, ADR-071)

| Stage | File | Output (strict JSON schema) | Model setting |
|---|---|---|---|
| Lesson map | `coverage.v2.md` | `LessonMap` (what the learner asked for, the lesson's shape: process, comparison, problem, procedure or concept, the one example every scene follows, the opening, the big idea, the teaching units, and the spine: the units of the one path through the request, in order) | the teacher's model, at `ANIMATOR_EFFORT_COVERAGE` (low) |
| Teacher | `teacher.v3.md` | `TeachingScript` (the whole explanation: every spoken sentence and what is seen, with the core idea, intuition, misconception and worked example) | `ANIMATOR_MODEL_TEACHER` (medium) |
| Reviewer | `review.v3.md` | `ScriptReview` (what was fixed, and the improved script) | `ANIMATOR_MODEL_REVIEWER` (low) |
| Edit (only a script still well over its budget, or asking more questions than it may) | `trim.v2.md` | `ScriptTrim` (those scenes' beats, shortened, the extra questions turned into statements) | the reviewer's model, low |
| Visual director | `director.v3.md` | `Direction` (the lesson's look and the set that stays, then each scene's composition, opening with the picture the last scene ended on, and what each beat shows), streamed | `ANIMATOR_MODEL_DIRECTOR` (medium) |
| Scenes | `animation.v8.md` + DSL reference + component catalog + exemplars | the engine's LLM-facing Scene schema | `ANIMATOR_MODEL_ANIMATION_PLANNER`, at `ANIMATOR_EFFORT_TEACHING_SCENE` (medium) |
| Correction | same instructions as the scenes | `ScenePatch` | `ANIMATOR_MODEL_CORRECTION`, final attempt `..._CORRECTION_FINAL` |
| Visual critic | `critic.v2.md` | `CriticReport` | `ANIMATOR_MODEL_VISUAL_CRITIC` |

- The plan for the lesson's length (`pipeline/depth.py`) is made by code from the coverage map: the depth
  (Snapshot up to 75 s, Essentials up to 150 s, Deep dive up to 270 s, Mastery up to 450 s, Masterclass
  beyond), the units that depth needs while they fit the time, in teaching order, cut into scenes of about
  15 s, each with a word budget at the pace the lesson's voice really speaks (`pipeline/pace.py`), after the
  1.2 s that ends every scene. What does not fit is the lesson's "Go deeper".
- The teacher's message gives that plan: the depth, the hook, the one idea to land, every scene's step, what
  it teaches and its word budget, and what is left for a longer lesson. Without a coverage map (switched
  off, or the call failed) the plan has the depth, the number of scenes and their budgets only.
- Lessons that flow (ADR-071, `ANIMATOR_LESSON_FLOW`, on by default): the plan keeps the lesson map's whole
  spine at every length, in its order (one step a scene up to one every 20 s; further steps share scenes
  with a neighbour, cut between steps), numbers its steps ("Step 2 of 6"; a comparison numbers only its
  differences), and puts the other units where they teach best (what is needed first before the path, a
  misconception right after its step, uses after the path). The teacher's and reviewer's messages add what
  the learner asked for, the shape, the through-line, the opening (instead of the hook), the path and the
  most questions the narration may ask (1 up to 2.5 minutes, 2 up to 4.5, 3, then 4); the reviewer sees
  the questions counted. Questions still over the budget are turned into statements by the edit call.
  The director's message adds the shape, the through-line and the path; quick checks are fewer (none
  in a 1-minute lesson, then 1, 1, 2, 3; with one, in the last scene). Each scene's message says that
  the scenes continue each other: what the brief's plan "Opens with" is created at 0 s, in place.
  The script schema describes the opening as a statement (not "the opening question or surprise").
- With `ANIMATOR_LESSON_FLOW=false` the teaching pipeline is as in ADR-063: `coverage.v1`, `teacher.v2`,
  `review.v2`, `director.v2`, `trim.v1`.
- The reviewer's message gives every scene's measured word count against its budget. Models cannot count
  words. A script still over 110% of its budget has its scenes that are over 112% of theirs trimmed.
- The director's message says how many quick checks the lesson has room for (never in the first scene,
  never two in a row); extra ones are left out by code.
- With `ANIMATOR_TIME_FIT=false` the teaching pipeline is as in ADR-044: `teacher.v1`, `review.v1`,
  `director.v1`, duration x 2.5 words per second over `max(3, duration / 14)` scenes, no coverage map, no trim.
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

### TeachQuiz (the evaluation runner only, ADR-070)

Never called while a lesson is made: `python -m animator.eval --quiz` (each new lesson) or `--score DIR`
(lessons already made) measures how much a lesson teaches.

| Step | File | Output (strict JSON schema) | Model setting |
|---|---|---|---|
| Quiz | `teachquiz.v1.md` | `Quiz` (multiple-choice questions on what a lesson of this length must teach: 4 for a Snapshot, 6, 8, 10, 12 for a Masterclass; four choices, one right) | `ANIMATOR_MODEL_QUIZ` (else the storyboard model), `ANIMATOR_EFFORT_QUIZ` (low) |
| Student | `student.v1.md` | `QuizAnswers` (a choice, or -1 "not taught", with the narrator's words or the scene picture that backs it) | the same |

- The quiz's message gives the topic, the audience, the length and its depth (the tier's brief), and the
  number of questions. A topic's quiz is kept (`var/eval/quizzes`), so lessons made with other settings are
  judged on the same questions.
- The student's message gives the lesson scene by scene (the narration) and the quiz; with `--pictures`,
  one keyframe of each scene follows as a small image. Code counts an answer only when it is right and the
  quote is the narrator's own words (80% of its word pairs) or the picture named was shown.

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
- what the learner asked for, in its shape (a process followed stage by stage, a comparison side by side, a
  problem solved step by step with the reason for each step), on one running example, in numbered steps
  that each start from where the last one ended (ADR-071);
- few questions (statements explain; a question only for a real prediction) and few quick checks, with
  misconceptions named and corrected where they arise;
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
