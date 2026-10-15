"""The teaching pipeline (ADR-044): a teacher writes the whole explanation, a reviewer checks and
improves it, a visual director plans every scene's picture, then the scenes are drawn, looked at
(rendered keyframes and the measured composition) and framed by the camera. The fast pipeline keeps
its own prompts."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from animator.engine_client import EngineClient, EngineWorkerPool
from animator.llm import LLMBadOutput, LLMRequest
from animator.pipeline.generator import Pipeline
from animator.pipeline.models import DirectedScene, TeachingScript
from animator.pipeline.stages import DirectionSink, length_note, script_brief, target_words
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
    assert labels[:3] == ["lesson script", "script review", "visual direction"]
    assert "outline" not in labels and not any(r.stage == "educational_planner" for r in script.calls)
    assert [s.status for s in gen.scenes] == ["ready"] * 3
    # The teaching stages use the storyboard's model unless they have their own.
    assert {r.stage: r.models[0] for r in script.calls[:3]} == {
        "teacher": "m-board",
        "reviewer": "m-board",
        "director": "m-board",
    }
    teacher = text_of(script.calls[0])
    assert f"about {target_words(30)} words in total" in teacher and "3 scenes" in teacher
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
    gen, _ = await run(pool, script, teaching(), duration_min=0.5)
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


# ---------------------------------------------------------------------- the critic and the camera


class Frames:
    """Keyframes for the visual critic (the images themselves do not matter to the fake critic)."""

    def __init__(self) -> None:
        self.calls = 0

    async def keyframes(self, scenes: list[dict[str, Any]], index: int, times: list[float]) -> list[bytes]:
        self.calls += 1
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
    pool: EngineWorkerPool, script: Teaching, frames: Frames | None, **kw: Any
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
