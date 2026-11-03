"""The teaching pipeline (ADR-044): a teacher writes the whole explanation, a reviewer checks and
improves it, a visual director plans every scene's picture, then the scenes are drawn, looked at
(rendered keyframes and the measured composition) and framed by the camera. The fast pipeline keeps
its own prompts. Lessons fit their length (ADR-063): a coverage map first, the teacher writes the
scenes that fit the time, each to a word budget, and a script well over its budget is trimmed."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from animator.engine_client import EngineClient, EngineWorkerPool
from animator.llm import LLMBadOutput, LLMRequest
from animator.pipeline.generator import KeyframeSource, Pipeline
from animator.pipeline.models import DirectedScene, TeachingScript
from animator.pipeline.stages import (
    CARRY_OVER,
    FLOW_HOOK,
    DirectionSink,
    length_note,
    script_brief,
    target_words,
)
from animator.pipeline.tidy import salvage, tidy
from tests.test_pipeline import SCENES, Script, run, settings, text_of

# ---------------------------------------------------------------------- fixtures


def teacher_script(words_per_beat: int = 6, scenes: int = 3) -> dict[str, Any]:
    filler = " ".join(["energy"] * max(0, words_per_beat - 4))
    return {
        "title": "Why things float",
        "subtitle": "Archimedes' principle",
        "subject": "physics",
        "learner": "a high-school student who knows weight",
        "hook": "Why does a steel ship float?",
        "learning_objectives": ["Explain buoyancy"],
        "core_idea": "Water pushes up with the weight of the water moved aside.",
        "intuition": "Pushing a ball under water",
        "misconception": "Heavy things always sink",
        "worked_example": "A 2 kg block moves 2 litres of water aside: 20 newtons up.",
        "key_terms": [{"term": "buoyant force", "meaning": "the push up from a fluid"}],
        "scenes": [
            {
                "title": f"Scene {k + 1}",
                "step": "concept",
                "goal": f"understand part {k + 1}",
                "beats": [
                    {
                        "say": f"Look at the **tank** number {k + 1} {filler}.",
                        "show": f"the tank {k + 1} appears",
                    },
                    {"say": f"Now the **block** sinks {filler}.", "show": "the block sinks"},
                ],
            }
            for k in range(scenes)
        ],
        "summary": ["It floats when the push up matches its weight"],
    }


def coverage() -> dict[str, Any]:
    """What there is to teach about floating, from what even a 1-minute lesson needs to extras."""

    def unit(
        uid: str, kind: str, title: str, priority: int, seconds: float, needs: list[str]
    ) -> dict[str, Any]:
        return {
            "id": uid,
            "kind": kind,
            "title": title,
            "teach": f"The learner understands {title.lower()}.",
            "priority": priority,
            "seconds": seconds,
            "needs": needs,
        }

    return {
        "big_idea": "Water pushes up with the weight of the water moved aside.",
        "hook": "Why does a steel ship float when a nail sinks?",
        "units": [
            unit("push", "intuition", "Pushing a ball under water", 1, 12, []),
            unit("buoyancy", "core", "The buoyant force", 1, 15, ["push"]),
            unit("block", "example", "A 2 kg block in water", 2, 25, ["buoyancy"]),
            unit("heavy", "misconception", "Heavy things always sink", 3, 20, ["buoyancy"]),
            unit("ships", "application", "Why ships float", 4, 20, ["buoyancy"]),
        ],
    }


def lesson_map() -> dict[str, Any]:
    """The same map as a flowing lesson's (ADR-071): what was asked, its shape, the example it follows
    and its path first."""
    return {
        "asked": "why a heavy steel ship floats",
        "shape": "concept",
        "through_line": "a 2 kg block lowered into a glass tank of water",
        "opening": "We lower a 2 kg block into a tank and watch the water push back.",
        "big_idea": coverage()["big_idea"],
        "units": coverage()["units"],
        "spine": ["push", "buoyancy"],
    }


def direction(scenes: int = 3) -> dict[str, Any]:
    return {
        "look": "A glass tank centred on the stage; blue water; the block is orange.",
        "scenes": [
            {
                "scene": k + 1,
                "layout": "stage_with_caption",
                "visual_plan": f"the tank big in the centre ({k + 1})",
                "objects_new": ["tank: a glass tank", "block: an orange block"],
                "physics": None,
                "beats": [f"the tank fades in, centred ({k + 1})", "the block drops into the water"],
                "engagement": "a highlight on the block",
                "checkpoint": None,
            }
            for k in range(scenes)
        ],
    }


class Teaching(Script):
    """Routes the teaching stages; scenes stand on their own (parallel mode)."""

    def __init__(self, **overrides: Any) -> None:
        super().__init__(lambda i, a, r: copy.deepcopy(SCENES[0]))
        self.overrides = overrides

    def __call__(self, req: LLMRequest) -> Any:
        label = req.label.split(" (retry")[0]
        if label in self.overrides:
            self.calls.append(req)
            out = self.overrides[label]
            return out(req) if callable(out) else copy.deepcopy(out)
        if label == "coverage plan":
            self.calls.append(req)
            return lesson_map() if req.prompt_version == "coverage.v2" else coverage()
        if label == "script timing":
            self.calls.append(req)
            return {"scenes": []}
        if label == "lesson script":
            self.calls.append(req)
            return teacher_script()
        if label == "script review":
            self.calls.append(req)
            reviewed = teacher_script()
            reviewed["scenes"][0]["beats"][0]["say"] = "Look closely at the **tank**, it holds water."
            return {"changes": ["Made the first sentence precise."], "script": reviewed}
        if label == "visual direction":
            self.calls.append(req)
            return direction()
        return super().__call__(req)


def teaching(**kw: Any) -> Any:
    return settings(
        animator_scene_mode="parallel",
        animator_pipeline="teaching",
        animator_model_visual_critic="m-look",
        **kw,
    )


# ---------------------------------------------------------------------- the whole flow


async def test_a_lesson_is_taught_reviewed_directed_then_drawn(pool: EngineWorkerPool) -> None:
    script = Teaching()
    gen, _ = await run(pool, script, teaching(), duration_min=0.5)
    assert gen.status == "complete", gen.error
    labels = [r.label for r in script.calls]
    assert labels[:4] == ["coverage plan", "lesson script", "script review", "visual direction"]
    assert "outline" not in labels and not any(r.stage == "educational_planner" for r in script.calls)
    assert [s.status for s in gen.scenes] == ["ready"] * 3
    # The teaching stages use the storyboard's model unless they have their own.
    assert {r.stage: r.models[0] for r in script.calls[:4]} == {
        "teacher": "m-board",
        "reviewer": "m-board",
        "director": "m-board",
    }
    teacher = text_of(script.calls[1])
    assert "Depth: Snapshot, a 30-second lesson" in teacher and "Plan for this length: exactly" in teacher
    # The narration is the reviewed script's, word for word; the pictures are the director's.
    first = gen.scenes[0].brief
    assert first.beats[0].narration == "Look closely at the **tank**, it holds water."
    assert first.beats[0].on_screen == "the tank fades in, centred (1)"
    assert first.visual_plan == "the tank big in the centre (1)" and first.title == "Scene 1"
    assert gen.plan is not None and gen.plan.title == "Why things float"
    assert gen.teaching is not None and gen.teaching["changes"] == ["Made the first sentence precise."]
    assert gen.teaching["look"].startswith("A glass tank")
    # Scenes are drawn with the teaching prompt at the teaching effort, knowing the whole lesson.
    draft = next(r for r in script.calls if r.label == "scene 2 attempt 1")
    assert draft.prompt_version == "animation.v8" and draft.effort == "medium"
    context = text_of(draft)
    assert "The lesson's look (keep it in every scene): A glass tank" in context
    assert "you are drawing scene 2" in context and "Look closely at the **tank**" in context


async def test_the_fast_pipeline_keeps_its_own_prompts_and_effort(pool: EngineWorkerPool) -> None:
    script = Script(lambda i, a, r: copy.deepcopy(SCENES[0]))
    gen, _ = await run(pool, script, settings(animator_scene_mode="parallel"), duration_min=0.5)
    assert gen.status == "complete" and gen.teaching is None
    draft = next(r for r in script.calls if r.label == "scene 1 attempt 1")
    assert draft.prompt_version == "animation.v5" and draft.effort == "low"
    assert "The lesson's look" not in text_of(draft)


async def test_a_failed_teacher_falls_back_to_the_fast_pipeline(pool: EngineWorkerPool) -> None:
    script = Teaching(**{"lesson script": LLMBadOutput("not a script")})
    gen, _ = await run(pool, script, teaching(), duration_min=0.5)
    assert gen.status == "complete", gen.error
    labels = [r.label for r in script.calls]
    assert (
        sum(lb.startswith("lesson script") for lb in labels) == 3 and "outline" in labels
    )  # retried, then the fast way
    assert gen.teaching is None and all(s.status == "ready" for s in gen.scenes)
    draft = next(r for r in script.calls if r.label == "scene 1 attempt 1")
    assert draft.prompt_version == "animation.v5"


async def test_a_failed_direction_draws_the_scenes_from_the_teachers_show_notes(
    pool: EngineWorkerPool,
) -> None:
    script = Teaching(**{"visual direction": LLMBadOutput("broken")})
    gen, _ = await run(pool, script, teaching(), duration_min=0.5)
    assert gen.status == "complete", gen.error
    assert len(gen.scenes) == 3 and all(s.status == "ready" for s in gen.scenes)
    assert gen.scenes[1].brief.beats[0].on_screen == "the tank 2 appears"


async def test_a_review_that_runs_far_too_long_keeps_the_teachers_script(pool: EngineWorkerPool) -> None:
    long = teacher_script(words_per_beat=60)
    script = Teaching(**{"script review": {"changes": ["Explained much more."], "script": long}})
    gen, _ = await run(pool, script, teaching(animator_time_fit=False), duration_min=0.5)
    assert gen.status == "complete", gen.error
    assert gen.teaching is not None and gen.teaching["changes"] == []
    assert gen.scenes[0].brief.beats[0].narration == "Look at the **tank** number 1 energy energy."
    review = next(r for r in script.calls if r.label == "script review")
    assert "Length: the script has 42 words of narration; the target is 75" in text_of(review)


async def test_the_review_can_be_switched_off(pool: EngineWorkerPool) -> None:
    script = Teaching()
    gen, _ = await run(pool, script, teaching(animator_script_review=False), duration_min=0.5)
    assert gen.status == "complete", gen.error
    assert "script review" not in [r.label for r in script.calls]
    assert gen.scenes[0].brief.beats[0].narration == "Look at the **tank** number 1 energy energy."


# ------------------------------------------------------------ lessons that fit their length (ADR-063)


def checked_direction() -> dict[str, Any]:
    """A direction that puts a quick check in every scene."""
    d = direction()
    for sc in d["scenes"]:
        sc["checkpoint"] = {
            "question": "Does it float?",
            "choices": ["yes", "no", "maybe"],
            "answer": 0,
            "explain": "It does.",
        }
    return d


def call(script: Teaching, label: str) -> LLMRequest:
    return next(r for r in script.calls if r.label == label)


async def test_a_lesson_is_planned_for_its_length_before_it_is_taught(pool: EngineWorkerPool) -> None:
    """ADR-063 as it was (the lesson flow of ADR-071 switched off)."""
    script = Teaching(**{"visual direction": checked_direction()})
    gen, _ = await run(pool, script, teaching(animator_lesson_flow=False), duration_min=0.5)
    assert gen.status == "complete", gen.error
    cover = call(script, "coverage plan")
    assert cover.stage == "teacher" and cover.effort == "low" and cover.prompt_version == "coverage.v1"
    assert "Lesson length: 30 seconds (0.5 minutes); map at least 45 seconds" in text_of(cover)
    teacher = call(script, "lesson script")
    assert teacher.prompt_version == "teacher.v2"
    told = text_of(teacher)
    assert "Depth: Snapshot, a 30-second lesson" in told and "Hook: Why does a steel ship float" in told
    assert "The buoyant force" in told.split("Left for a longer lesson")[0]  # what a 30 s lesson teaches
    assert "A 2 kg block in water" in told.split("Left for a longer lesson")[1]  # and what it leaves
    review = call(script, "script review")
    assert review.prompt_version == "review.v2" and "Scene words against their budgets" in text_of(review)
    director = call(script, "visual direction")
    assert director.prompt_version == "director.v2" and "Quick checks: up to 1 in the lesson" in text_of(
        director
    )
    # One quick check, never in the first scene: the others are left out.
    assert [s.brief.checkpoint is not None for s in gen.scenes] == [False, True, False]
    assert gen.teaching is not None
    depth = gen.teaching["depth"]
    # 30 seconds hold one idea, properly: the analogy is left for a longer lesson.
    assert depth["tier"] == "snapshot" and depth["chosen"] == ["buoyancy"] and depth["trimmed"] == 0
    assert depth["words"] == sum(s["words"] for s in depth["scenes"]) and depth["wps"] == 2.5  # no voice
    summary = gen.summary()["depth"]
    assert summary["name"] == "Snapshot" and summary["deeper"][0]["title"] == "Pushing a ball under water"
    timing = gen.teaching["timing"]
    assert timing["asked_s"] == 30 and timing["length_s"] > 0 and timing["voiced"] == 0


async def test_without_time_fit_the_teaching_pipeline_is_as_before(pool: EngineWorkerPool) -> None:
    script = Teaching(**{"visual direction": checked_direction()})
    gen, _ = await run(pool, script, teaching(animator_time_fit=False), duration_min=0.5)
    assert gen.status == "complete", gen.error
    assert [r.label for r in script.calls][:3] == ["lesson script", "script review", "visual direction"]
    versions = {r.label: r.prompt_version for r in script.calls[:3]}
    assert versions == {
        "lesson script": "teacher.v1",
        "script review": "review.v1",
        "visual direction": "director.v1",
    }
    teacher = text_of(script.calls[0])
    assert f"about {target_words(30)} words in total" in teacher and "3 scenes" in teacher
    assert "Quick checks" not in text_of(script.calls[2])
    assert all(s.brief.checkpoint is not None for s in gen.scenes)  # as the director planned
    assert gen.teaching is not None and "depth" not in gen.teaching and gen.summary()["depth"] is None


async def test_a_failed_coverage_map_still_plans_the_lesson_by_its_length(pool: EngineWorkerPool) -> None:
    script = Teaching(**{"coverage plan": LLMBadOutput("not a map")})
    gen, _ = await run(pool, script, teaching(), duration_min=0.5)
    assert gen.status == "complete", gen.error
    assert sum(r.label.startswith("coverage plan") for r in script.calls) == 2  # retried once
    told = text_of(call(script, "lesson script"))
    assert "Depth: Snapshot" in told and "your choice" in told and "Left for a longer lesson" not in told
    assert gen.teaching is not None and gen.teaching["depth"]["chosen"] == []


async def test_a_coverage_map_can_be_switched_off(pool: EngineWorkerPool) -> None:
    script = Teaching()
    gen, _ = await run(pool, script, teaching(animator_coverage_plan=False), duration_min=0.5)
    assert gen.status == "complete", gen.error
    assert "coverage plan" not in [r.label for r in script.calls]
    assert "Depth: Snapshot" in text_of(call(script, "lesson script"))


@pytest.mark.parametrize("flow", [True, False])
async def test_a_script_well_over_its_budget_is_trimmed_before_it_is_drawn(
    pool: EngineWorkerPool, flow: bool
) -> None:
    long = teacher_script(words_per_beat=40)
    short = [
        {"say": "Look, the **tank** fills.", "show": "the tank fills"},
        {"say": "It rises.", "show": "x"},
    ]
    trim = {"scenes": [{"scene": k, "beats": short} for k in (1, 2, 3)]}
    script = Teaching(
        **{"lesson script": long, "script review": {"changes": [], "script": long}, "script timing": trim}
    )
    gen, _ = await run(pool, script, teaching(animator_lesson_flow=flow), duration_min=0.5)
    assert gen.status == "complete", gen.error
    timing = call(script, "script timing")
    assert timing.prompt_version == ("trim.v2" if flow else "trim.v1")
    assert timing.stage == "reviewer" and timing.effort == "low"
    assert "Scene 1 (concept, 'Scene 1'): 82 words, budget" in text_of(timing)
    assert gen.teaching is not None and gen.teaching["depth"]["trimmed"] == 3
    assert gen.teaching["depth"]["words_written"] == 3 * 6
    assert [b.narration for b in gen.scenes[0].brief.beats] == ["Look, the **tank** fills.", "It rises."]


async def test_a_failed_trim_keeps_the_script(pool: EngineWorkerPool) -> None:
    long = teacher_script(words_per_beat=40)
    script = Teaching(
        **{
            "lesson script": long,
            "script review": {"changes": [], "script": long},
            "script timing": LLMBadOutput("no"),
        }
    )
    gen, _ = await run(pool, script, teaching(), duration_min=0.5)
    assert gen.status == "complete", gen.error
    assert gen.teaching is not None and gen.teaching["depth"]["trimmed"] == 0
    assert gen.scenes[0].brief.beats[0].narration.startswith("Look at the **tank** number 1")


# ------------------------------------------------------------------------- lessons that flow (ADR-071)


async def test_a_flowing_lesson_answers_what_was_asked_along_one_path(pool: EngineWorkerPool) -> None:
    script = Teaching(**{"visual direction": checked_direction()})
    gen, _ = await run(pool, script, teaching(), duration_min=0.5)
    assert gen.status == "complete", gen.error
    cover = call(script, "coverage plan")
    assert cover.prompt_version == "coverage.v2" and cover.schema_name == "LessonMap"
    assert "The learner's request, in their words: " in text_of(cover)
    teacher = call(script, "lesson script")
    assert teacher.prompt_version == "teacher.v3"
    told = text_of(teacher)
    assert "What the learner asked for: why a heavy steel ship floats" in told
    assert (
        "Lesson shape: concept" in told and "Opening: We lower a 2 kg block" in told and "Hook:" not in told
    )
    assert "Through-line (the one example every scene follows): a 2 kg block lowered" in told
    assert "Part 1 of 2, Pushing a ball under water" in told and "Part 2 of 2, The buoyant force" in told
    assert "Questions: at most 1 in the whole narration" in told
    # The script's opening is described as what it now is: a statement, not "the opening question".
    assert teacher.schema["properties"]["hook"]["description"] == FLOW_HOOK
    review = call(script, "script review")
    assert review.prompt_version == "review.v3"
    assert "Questions: the narration asks 0 (at most 1). Keep it so." in text_of(review)
    assert review.schema["$defs"]["TeachingScript"]["properties"]["hook"]["description"] == FLOW_HOOK
    director = call(script, "visual direction")
    seen = text_of(director)
    assert director.prompt_version == "director.v3" and "Lesson shape: concept" in seen
    assert "The path, in order: 1. Pushing a ball under water; 2. The buoyant force." in seen
    assert "Through-line (on screen in every scene it takes part in" in seen
    # A 30-second lesson stops for no quick check, though the director wrote one in every scene.
    assert "Quick checks: none in this lesson." in seen
    assert all(s.brief.checkpoint is None for s in gen.scenes)
    assert gen.teaching is not None
    depth = gen.teaching["depth"]
    assert depth["flow"] and depth["shape"] == "concept" and depth["spine"] == ["push", "buoyancy"]
    assert depth["chosen"] == ["push", "buoyancy"] and depth["questions_asked"] == 0  # the whole path
    # Every scene is drawn knowing the lesson's scenes continue each other.
    draft = call(script, "scene 2 attempt 1")
    assert CARRY_OVER in text_of(draft) and draft.prompt_version == "animation.v8"


async def test_questions_over_the_budget_become_statements_before_anything_is_drawn(
    pool: EngineWorkerPool,
) -> None:
    asking = teacher_script()
    asking["scenes"][0]["beats"][0]["say"] = "What does the **tank** hold? Water."
    asking["scenes"][1]["beats"][1]["say"] = "Why does the **block** sink? It is heavy."
    asking["scenes"][2]["beats"][0]["say"] = "Can you guess the **tank** number? It is three."
    stated = {
        "scenes": [
            {
                "scene": 2,
                "beats": [
                    {"say": "Look at the **tank** number 2 energy energy.", "show": "x"},
                    {"say": "Here is why the **block** sinks, it is heavy.", "show": "y"},
                ],
            },
            {
                "scene": 3,
                "beats": [
                    {"say": "The **tank** number is three.", "show": "x"},
                    {"say": "Now the **block** sinks energy energy.", "show": "y"},
                ],
            },
        ]
    }
    script = Teaching(
        **{
            "lesson script": asking,
            "script review": {"changes": [], "script": asking},
            "script timing": stated,
        }
    )
    gen, _ = await run(pool, script, teaching(), duration_min=0.5)
    assert gen.status == "complete", gen.error
    note = text_of(call(script, "script review"))
    assert "Questions: the narration asks 3 questions (in scenes 1, 2, 3); at most 1 may stay" in note
    edit = call(script, "script timing")
    told = text_of(edit)
    assert edit.prompt_version == "trim.v2" and "Scene 1 (" not in told  # the one question allowed stays
    assert 'turn into statements: "Why does the **block** sink?"' in told
    assert 'turn into statements: "Can you guess the **tank** number?"' in told
    said = [b.narration for s in gen.scenes for b in s.brief.beats]
    assert "Here is why the **block** sinks, it is heavy." in said and "The **tank** number is three." in said
    assert "What does the **tank** hold? Water." in said
    assert gen.teaching is not None and gen.teaching["depth"]["questions_asked"] == 1
    assert gen.teaching["depth"]["trimmed"] == 2


async def test_without_the_flow_scenes_are_not_told_to_continue_each_other(pool: EngineWorkerPool) -> None:
    script = Teaching()
    gen, _ = await run(pool, script, teaching(animator_lesson_flow=False), duration_min=0.5)
    assert gen.status == "complete", gen.error
    assert CARRY_OVER not in text_of(call(script, "scene 2 attempt 1"))
    assert gen.teaching is not None and "flow" not in gen.teaching["depth"]
    assert call(script, "lesson script").schema["properties"]["hook"]["description"] != FLOW_HOOK


# ---------------------------------------------------------------------- the critic and the camera


class Frames:
    """Keyframes for the visual critic (the images themselves do not matter to the fake critic)."""

    def __init__(self) -> None:
        self.calls = 0
        self.finishes: list[str] = []

    async def keyframes(
        self, scenes: list[dict[str, Any]], index: int, times: list[float], finish: str = "classic"
    ) -> list[bytes]:
        self.calls += 1
        self.finishes.append(finish)
        return [b"png"] * len(times)


def corner_scene() -> dict[str, Any]:
    """A valid scene whose whole picture is one small dot in the top-left of the stage."""
    return {
        "dsl_version": "1.0",
        "id": "x",
        "title": "A dot",
        "duration": 6,
        "layout": "stage_with_caption",
        "narration": {
            "beats": [{"id": "b1", "text": "Look at the **dot**."}, {"id": "b2", "text": "It is small."}]
        },
        "objects": [
            {
                "id": "title_s01",
                "type": "text",
                "params": {"text": "A dot", "size": "heading", "weight": "bold"},
                "place": {"slot": "title_bar"},
                "layer": "overlay",
            },
            {"id": "dot", "type": "circle", "params": {"radius": 30}, "place": {"x": 260, "y": 260}},
        ],
        "timeline": [
            {"id": "t0", "action": "write", "target": "title_s01", "start": {"at": 0.2}},
            {"id": "a1", "action": "create", "target": "dot", "start": {"at": "beat:b1"}},
        ],
    }


async def run_teaching(
    pool: EngineWorkerPool, script: Teaching, frames: KeyframeSource | None, **kw: Any
) -> tuple[Any, Any]:
    from animator.jobs.runner import GenerationRunner
    from animator.jobs.store import MemoryStore
    from animator.llm import FakeLLM, Usage
    from animator.pipeline.models import GenerationRequest

    s = teaching(**kw)
    fake = FakeLLM(script, usage=Usage(1000, 0, 500), cost=0.01)
    runner = GenerationRunner(MemoryStore(), Pipeline(EngineClient(pool), fake, s, frames), s)
    gen = await runner.start(GenerationRequest(topic="Archimedes' principle", duration_min=0.5))
    import asyncio

    await asyncio.wait_for(runner._tasks[gen.id], timeout=120)
    return await runner.get(gen.id), runner


async def test_a_small_off_centre_picture_is_framed_by_the_camera(pool: EngineWorkerPool) -> None:
    script = Teaching()
    script.scene = lambda i, a, r: corner_scene()
    gen, _ = await run_teaching(pool, script, None)
    assert gen.status == "complete", gen.error
    dsl = gen.scenes[0].dsl
    assert dsl is not None and dsl["camera"]["zoom"] == pytest.approx(1.5)
    assert dsl["camera"]["center"]["x"] == pytest.approx(260, abs=2)  # the dot is now in the middle


async def test_camera_framing_can_be_switched_off(pool: EngineWorkerPool) -> None:
    script = Teaching()
    script.scene = lambda i, a, r: corner_scene()
    gen, _ = await run_teaching(pool, script, None, animator_auto_frame=False)
    assert gen.status == "complete", gen.error
    dsl = gen.scenes[0].dsl
    assert dsl is not None and dsl["camera"]["zoom"] == 1


async def test_a_poor_composition_is_sent_back_even_when_the_critic_passes_it(pool: EngineWorkerPool) -> None:
    script = Teaching()
    script.scene = lambda i, a, r: corner_scene()
    frames = Frames()
    # Without the camera's framing the dot stays small in the corner, for the critic to see.
    gen, _ = await run_teaching(pool, script, frames, animator_auto_frame=False)
    assert gen.status == "complete", gen.error
    assert frames.calls >= 3
    fix = next(r for r in script.calls if r.label == "scene 1 attempt 2")
    note = text_of(fix)
    assert "A reviewer asked for these changes" in note and "The composition is poor" in note
    critic = next(r for r in script.calls if r.label == "critic scene 1")
    assert critic.prompt_version == "critic.v2" and "Composition, measured by the engine" in text_of(critic)
    assert all(s.status == "ready" for s in gen.scenes)


async def test_a_critics_revision_that_never_works_keeps_the_scene_it_reviewed(
    pool: EngineWorkerPool,
) -> None:
    script = Teaching(
        **{
            "critic scene 1": {
                "verdict": "revise",
                "issues": [{"severity": "major", "problem": "The block is hidden.", "fix": "Move it up."}],
            }
        }
    )

    def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        if i == 0 and attempt > 1:
            return {
                "objects": [],
                "delete": ["no_such_id"],
                "timeline": [{"id": "x", "action": "create", "target": "nothing"}],
            }
        return copy.deepcopy(SCENES[0])

    script.scene = scene
    gen, _ = await run_teaching(pool, script, Frames())
    assert gen.status == "complete", gen.error
    first = gen.scenes[0]
    assert first.status == "ready"  # not the simplified fallback
    assert first.attempts[0].critic is not None and first.attempts[0].critic["verdict"] == "revise"


# ---------------------------------------------------------------------- pieces


def test_the_direction_streams_the_look_and_each_scene_once() -> None:
    got: list[Any] = []
    sink = DirectionSink(lambda look: got.append(("look", look)), lambda d: got.append(("scene", d.scene)))
    text = '{"look": "A \\"glass\\" tank", "scenes": [' + ",".join(
        __import__("json").dumps(s) for s in direction(2)["scenes"]
    )
    sink.feed(text[:60])
    sink.feed(text[60:] + "]}")
    sink.reset()
    sink.feed(text + "]}")  # a retry streams the same scenes again
    assert got == [("look", 'A "glass" tank'), ("scene", 1), ("scene", 2)]


def test_a_brief_keeps_the_scripts_words_and_uses_the_teachers_notes_when_there_is_no_plan() -> None:
    script = TeachingScript.model_validate(teacher_script())
    planned = DirectedScene.model_validate({**direction()["scenes"][0], "beats": ["the tank fades in"]})
    brief = script_brief(script.scenes[0], planned)
    assert [b.narration for b in brief.beats] == [b.say for b in script.scenes[0].beats]
    assert [b.on_screen for b in brief.beats] == ["the tank fades in", "the block sinks"]  # missing: notes
    plain = script_brief(script.scenes[0])
    assert plain.layout == "stage_with_caption" and plain.beats[0].on_screen == "the tank 1 appears"
    assert 8 <= brief.duration_s <= 60


def test_the_reviewer_is_told_the_measured_length() -> None:
    short = TeachingScript.model_validate(teacher_script(words_per_beat=4))
    assert "Use the room" in length_note(short, 150)
    long = TeachingScript.model_validate(teacher_script(words_per_beat=40))
    assert "Shorten it to about 75 words" in length_note(long, 75)


def test_escaped_line_breaks_become_real_ones_but_tex_keeps_its_backslashes() -> None:
    dsl = {
        "objects": [
            {"id": "code", "type": "text", "params": {"text": "const a =\\nb;"}},
            {"id": "card", "type": "card", "params": {"title": "T", "items": ["one\\ntwo"]}},
            {"id": "eq", "type": "equation", "params": {"parts": [{"id": "p", "tex": "a \\neq b"}]}},
        ],
        "timeline": [
            {
                "id": "a",
                "action": "animate",
                "target": "code",
                "property": "text",
                "keyframes": [{"t": 0, "value": "x\\ny"}],
            }
        ],
    }
    out = tidy(dsl)
    assert out["objects"][0]["params"]["text"] == "const a =\nb;"
    assert out["objects"][1]["params"]["items"] == ["one\ntwo"]
    assert out["objects"][2]["params"]["parts"][0]["tex"] == "a \\neq b"
    assert out["timeline"][0]["keyframes"][0]["value"] == "x\ny"


def overlapping_labels_scene() -> dict[str, Any]:
    """A scene whose two labels sit on top of each other, in every attempt."""
    scene = corner_scene()
    scene["objects"][1]["place"] = {"slot": "stage.center"}
    scene["objects"] += [
        {"id": "first_label", "type": "label", "params": {"text": "Oxygen"}, "place": {"x": 800, "y": 300}},
        {
            "id": "second_label",
            "type": "label",
            "params": {"text": "Oxygen too"},
            "place": {"x": 805, "y": 302},
        },
    ]
    scene["timeline"] += [
        {"id": "l1", "action": "create", "target": "first_label", "start": {"at": "beat:b1"}},
        {"id": "l2", "action": "create", "target": "second_label", "start": {"after": "l1"}},
        {"id": "l3", "action": "create", "target": "dot", "start": {"with": "l2"}},
    ]
    scene["timeline"] = [a for a in scene["timeline"] if a["id"] != "a1"]
    return scene


async def test_labels_that_never_stop_overlapping_are_left_out_instead_of_simplifying_the_scene(
    pool: EngineWorkerPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("animator.pipeline.generator.autofix", lambda dsl, errors: None)  # no sure fix
    script = Teaching()
    script.scene = lambda i, a, r: overlapping_labels_scene()
    gen, _ = await run_teaching(pool, script, None)
    assert gen.status == "complete", gen.error
    first = gen.scenes[0]
    assert first.status == "ready" and len(first.attempts) == 4  # every correction was tried first
    assert first.dsl is not None
    ids = [o["id"] for o in first.dsl["objects"]]
    assert "first_label" in ids and "second_label" not in ids and "dot" in ids


def test_salvage_leaves_out_the_later_label_only_when_nothing_depends_on_it() -> None:
    scene = overlapping_labels_scene()
    errors = [{"code": "LABEL_OVERLAP", "message": "'first_label' and 'second_label' overlap at 1.2s."}]
    out = salvage(scene, errors)
    assert out is not None
    assert [o["id"] for o in out["objects"]] == ["title_s01", "dot", "first_label"]
    dot = next(a for a in out["timeline"] if a["id"] == "l3")
    assert dot["start"] == {"after": "l1"}  # it started with the dropped label: now where that one did
    pinned = overlapping_labels_scene()
    pinned["objects"][1]["place"] = {"relativeTo": "second_label", "side": "below"}
    assert salvage(pinned, errors) is None  # the dot is placed relative to it
    assert salvage(scene, [{"code": "UNKNOWN_OBJECT", "message": "No object with id 'x'."}]) is None
