"""The generation pipeline end to end with a scripted LLM and the real engine worker.

The fake "animation planner" answers with the hand-authored Archimedes scenes, so continuity
between scenes (carried objects, solver state) is exercised for real (spec Section 19:
correction loop, degraded fallback, refusal, budget stop, outage, cancellation, resume).
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from animator import REPO_ROOT
from animator.config import Settings
from animator.engine_client import EngineClient, EngineWorkerPool
from animator.engine_client.errors import WorkerCrashed
from animator.jobs.runner import GenerationRunner
from animator.jobs.store import FileStore, MemoryStore
from animator.llm import FakeLLM, LLMAuthError, LLMRefusal, LLMRequest, LLMUnavailable, Usage
from animator.pipeline.generator import Pipeline, fallback_scene, scene_records
from animator.pipeline.models import Generation, GenerationRequest, StoryboardChapter, StoryboardScene

FIX = REPO_ROOT / "fixtures" / "scenes" / "archimedes"
SCENES = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(FIX.glob("[0-9][0-9]-*.json"))]


def settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "animator_model_educational_planner": "m-plan",
        "animator_model_storyboard": "m-board",
        "animator_model_animation_planner": "m-scene",
        "animator_model_correction": "m-fix",
        "animator_model_correction_final": "m-final",
        "animator_model_critic": "m-critic",
        "animator_max_correction_attempts": 3,
        # The Archimedes fixtures continue from each other; parallel mode has its own tests below.
        "animator_scene_mode": "sequential",
        "animator_scene_drafts": 1,  # call counts below assume one draft; hedging has its own tests
        "animator_correction_drafts": 1,
        # These tests cover the fast pipeline; the teaching pipeline has its own (test_teaching.py).
        "animator_pipeline": "fast",
        # And speech as the provider makes it; voice modulation has its own tests (test_prosody.py).
        "animator_tts_prosody": False,
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


def plan(chapters: int = 2) -> dict[str, Any]:
    return {
        "title": "Why things float",
        "subtitle": "Archimedes' principle",
        "subject": "physics",
        "audience": "high-school student",
        "target_duration_s": 80.0,
        "hook": "Why does a steel ship float?",
        "learning_objectives": ["Explain buoyancy"],
        "prerequisites": [],
        "key_terms": [{"term": "buoyant force", "meaning": "the push up from a fluid"}],
        "misconceptions": ["heavy things always sink"],
        "chapters": [
            {
                "id": f"c{i + 1}",
                "title": f"Chapter {i + 1}",
                "goal": "understand",
                "key_ideas": ["idea"],
                "target_duration_s": 40.0,
                "visual_approach": "a tank of water",
            }
            for i in range(chapters)
        ],
        "summary": ["It floats when the push up matches its weight"],
    }


def outline_plan() -> dict[str, Any]:
    return {
        "title": "Why things float",
        "subtitle": "Archimedes' principle",
        "subject": "physics",
        "hook": "Why does a steel ship float?",
        "learning_objectives": ["Explain buoyancy"],
        "visual_approach": "a tank of water",
    }


def brief(title: str, transition: str = "continue") -> dict[str, Any]:
    return {
        "title": title,
        "purpose": f"Show {title.lower()}",
        "duration_s": 20.0,
        "layout": "stage_with_caption",
        "transition": transition,
        "visual_plan": "a tank of water and a block",
        "objects_new": ["tank: a glass tank"],
        "objects_kept": [],
        "objects_removed": [],
        "physics": None,
        "beats": [{"narration": "Look at the **tank**.", "on_screen": "the tank appears"}],
        "engagement": "a face on the block",
        "checkpoint": None,
    }


class Script:
    """Routes fake LLM calls by stage; `scene(index, attempt, request)` supplies scene DSL."""

    def __init__(self, scene: Callable[[int, int, LLMRequest], Any] | None = None, chapters: int = 2) -> None:
        self.chapters = chapters
        self.scene = scene or (lambda i, a, r: copy.deepcopy(SCENES[i]))
        self.calls: list[LLMRequest] = []

    def __call__(self, req: LLMRequest) -> Any:
        self.calls.append(req)
        if req.stage == "educational_planner":
            return plan(self.chapters)
        if req.label == "outline":
            return {
                "plan": outline_plan(),
                "scenes": [brief("Scene 1", "fade"), brief("Scene 2", "fade")],
                "summary": ["It floats when the push up matches its weight"],
            }
        if req.stage == "storyboard":
            chapter = "c1" if "chapter now: c1" in req.messages[0].parts[0].text else "c2"  # type: ignore[union-attr]
            k = 0 if chapter == "c1" else 2
            return {"chapter_id": chapter, "scenes": [brief(f"Scene {k + 1}"), brief(f"Scene {k + 2}")]}
        if req.stage in ("critic", "visual_critic"):
            return {"verdict": "pass", "issues": []}
        index = int(req.label.split()[1]) - 1
        attempt = int(req.label.split()[3])
        return self.scene(index, attempt, req)


async def run(
    pool: EngineWorkerPool,
    script: Callable[[LLMRequest], Any],
    s: Settings | None = None,
    store: Any = None,
    **req: Any,
) -> tuple[Generation, GenerationRunner]:
    fake = FakeLLM(script, usage=Usage(1000, 0, 500), cost=0.01)
    runner = GenerationRunner(
        store or MemoryStore(), Pipeline(EngineClient(pool), fake, s or settings()), s or settings()
    )
    gen = await runner.start(GenerationRequest(topic="Archimedes' principle", **req))
    await asyncio.wait_for(runner._tasks[gen.id], timeout=120)
    got = await runner.get(gen.id)
    assert got is not None
    return got, runner


def stages_for(script: Script, index: int) -> list[str]:
    return [r.stage for r in script.calls if r.label.startswith(f"scene {index + 1} ")]


# ---------------------------------------------------------------------- happy path


async def test_topic_to_playable_lesson(pool: EngineWorkerPool) -> None:
    script = Script()
    gen, runner = await run(pool, script)
    assert gen.status == "complete", gen.error
    assert [s.status for s in gen.scenes] == ["ready"] * 4
    assert [s.id for s in gen.scenes] == ["s01_scene_1", "s02_scene_2", "s03_scene_3", "s04_scene_4"]
    assert all(s.dsl and s.dsl["id"] == s.id for s in gen.scenes)
    assert gen.scenes[1].state_summary and "tank" in gen.scenes[1].state_summary  # continuity context
    assert gen.metrics.llm_calls == 1 + 2 + 4 and gen.metrics.cost_usd == pytest.approx(0.07)
    assert gen.metrics.first_play_s is not None
    calls = await runner.store.calls(gen.id)
    assert [c["stage"] for c in calls][:2] == ["educational_planner", "storyboard"]
    # the second scene is told what the first left on stage
    scene2 = next(r for r in script.calls if r.label == "scene 2 attempt 1")
    assert "Previous scene: s01_scene_1" in scene2.messages[0].parts[0].text  # type: ignore[union-attr]


async def test_models_follow_the_stage_configuration(pool: EngineWorkerPool) -> None:
    script = Script()
    await run(pool, script)
    models = {r.stage: r.models[0] for r in script.calls}
    assert models == {
        "educational_planner": "m-plan",
        "storyboard": "m-board",
        "animation_planner": "m-scene",
    }


# ---------------------------------------------------------------------- correction loop


def broken(scene: dict[str, Any]) -> dict[str, Any]:
    bad = copy.deepcopy(scene)
    bad["timeline"][0]["target"] = "no_such_object"
    return bad


async def test_engine_errors_are_fed_back_and_corrected(pool: EngineWorkerPool) -> None:
    def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        return broken(SCENES[i]) if i == 0 and attempt == 1 else copy.deepcopy(SCENES[i])

    script = Script(scene)
    gen, _ = await run(pool, script)
    s1 = gen.scenes[0]
    assert s1.status == "ready" and len(s1.attempts) == 2
    assert s1.attempts[0].errors and not s1.attempts[1].errors
    fix = next(r for r in script.calls if r.label == "scene 1 attempt 2")
    text = fix.messages[0].parts[-1].text  # type: ignore[union-attr]
    assert fix.stage == "correction" and "no_such_object" in text
    assert gen.metrics.corrections == 1


async def test_the_final_attempt_escalates_then_a_flagged_fallback_keeps_the_lesson_going(
    pool: EngineWorkerPool,
) -> None:
    script = Script(lambda i, a, r: broken(SCENES[i]) if i == 1 else copy.deepcopy(SCENES[i]))
    gen, _ = await run(pool, script)
    assert stages_for(script, 1) == ["animation_planner", "correction", "correction", "correction_final"]
    assert [r.models[0] for r in script.calls if r.label.startswith("scene 2 ")][-1] == "m-final"
    s2 = gen.scenes[1]
    assert s2.status == "degraded" and s2.degraded_reason and s2.dsl is not None
    assert s2.dsl["objects"][2]["params"]["text"] == "Simplified scene"  # flagged on screen, never silent
    # The lesson keeps going: later scenes are still generated. (The fallback starts a clean stage;
    # the scripted fixtures cannot adapt to that, so they fall back too. A real model sees the new
    # stage in the state summary and rebuilds it.)
    assert stages_for(script, 2) and stages_for(script, 3)
    assert gen.status == "complete"
    assert gen.metrics.degraded == sum(s.status == "degraded" for s in gen.scenes)


async def test_a_refusal_counts_as_an_attempt_and_gets_a_repair_prompt(pool: EngineWorkerPool) -> None:
    def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        if i == 0 and attempt == 1:
            return LLMRefusal("model refused: no")
        return copy.deepcopy(SCENES[i])

    script = Script(scene)
    gen, _ = await run(pool, script)
    assert gen.scenes[0].status == "ready" and gen.scenes[0].attempts[0].llm_error
    repair = next(r for r in script.calls if r.label == "scene 1 attempt 2")
    assert "could not be used" in repair.messages[0].parts[-1].text  # type: ignore[union-attr]


def test_fallback_scene_is_valid_dsl() -> None:
    board = StoryboardChapter(
        chapter_id="c1", scenes=[StoryboardScene.model_validate(brief("A tricky idea"))]
    )
    gen = Generation(request=GenerationRequest(topic="x" * 10))
    rec = scene_records(gen, board)[0]
    fb = fallback_scene(rec)
    assert fb["id"] == "s01_a_tricky_idea" and fb["transition_in"]["type"] == "fade"


# ---------------------------------------------------------------------- hard stops


async def test_budget_is_a_hard_stop(pool: EngineWorkerPool) -> None:
    script = Script()
    gen, _ = await run(pool, script, settings(llm_budget_usd_per_generation=0.045))
    assert gen.status == "failed" and "budget" in (gen.error or "")
    assert len(script.calls) < 7


async def test_a_rejected_api_key_fails_the_generation_clearly(pool: EngineWorkerPool) -> None:
    def auth(req: LLMRequest) -> Any:
        return LLMAuthError("OpenAI rejected the API key: the account has been deactivated")

    gen, _ = await run(pool, auth)
    assert gen.status == "failed" and "deactivated" in (gen.error or "")
    assert gen.plan is None and gen.scenes == []


async def test_an_outage_pauses_and_resume_continues_without_redoing_work(pool: EngineWorkerPool) -> None:
    outage = {"on": True}

    def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        if i == 2 and outage["on"]:
            return LLMUnavailable("OpenAI call failed after 4 retries: 503")
        return copy.deepcopy(SCENES[i])

    script = Script(scene)
    gen, runner = await run(pool, script)
    assert gen.status == "paused" and "resume" in (gen.error or "")
    assert [s.status for s in gen.scenes] == ["ready", "ready", "planned", "planned"]
    outage["on"] = False
    await runner.resume(gen.id)
    await asyncio.wait_for(runner._tasks[gen.id], timeout=120)
    done = await runner.get(gen.id)
    assert done is not None and done.status == "complete"
    assert len(stages_for(script, 0)) == 1  # scene 1 was not regenerated


async def test_cancel_stops_a_running_generation(pool: EngineWorkerPool) -> None:
    gate = asyncio.Event()

    async def slow(i: int, attempt: int, req: LLMRequest) -> Any:
        if i == 1:
            await gate.wait()
        return copy.deepcopy(SCENES[i])

    fake = FakeLLM(Script(slow))
    runner = GenerationRunner(MemoryStore(), Pipeline(EngineClient(pool), fake, settings()), settings())
    gen = await runner.start(GenerationRequest(topic="Archimedes' principle"))
    for _ in range(200):
        g = await runner.get(gen.id)
        if g and g.scenes and g.scenes[1].status == "generating":
            break
        await asyncio.sleep(0.05)
    cancelled = await runner.cancel(gen.id)
    assert cancelled is not None and cancelled.status == "cancelled"
    assert cancelled.scenes[0].status == "ready" and cancelled.scenes[1].status == "planned"


async def test_a_restarted_server_resumes_from_the_files(pool: EngineWorkerPool, tmp_path: Path) -> None:
    gate = asyncio.Event()

    async def slow(i: int, attempt: int, req: LLMRequest) -> Any:
        if i == 2 and not gate.is_set():
            await asyncio.sleep(3600)
        return copy.deepcopy(SCENES[i])

    store = FileStore(tmp_path)
    runner = GenerationRunner(
        store, Pipeline(EngineClient(pool), FakeLLM(Script(slow)), settings()), settings()
    )
    gen = await runner.start(GenerationRequest(topic="Archimedes' principle"))
    for _ in range(400):
        g = await runner.get(gen.id)
        if g and len(g.scenes) > 2 and g.scenes[2].status == "generating":
            break
        await asyncio.sleep(0.05)
    await runner.close()  # server stops
    saved = await store.get(gen.id)
    assert saved is not None and saved.status == "paused"

    gate.set()
    script = Script()
    fresh = GenerationRunner(
        FileStore(tmp_path), Pipeline(EngineClient(pool), FakeLLM(script), settings()), settings()
    )
    saved.status = "generating"  # as a crash would leave it
    await FileStore(tmp_path).save(saved)
    assert await fresh.resume_all() == [gen.id]
    await asyncio.wait_for(fresh._tasks[gen.id], timeout=120)
    done = await fresh.get(gen.id)
    assert done is not None and done.status == "complete"
    assert [r.label for r in script.calls] == ["scene 3 attempt 1", "scene 4 attempt 1"]


# ---------------------------------------------------------------------- critic and pacing


async def test_a_critic_revision_spends_one_more_attempt(pool: EngineWorkerPool) -> None:
    reviews = iter(
        [
            {
                "verdict": "revise",
                "issues": [{"severity": "major", "problem": "the tank is tiny", "fix": "make it bigger"}],
            }
        ]
    )

    class Critical(Script):
        def __call__(self, req: LLMRequest) -> Any:
            if req.stage == "critic":
                self.calls.append(req)
                return next(reviews, {"verdict": "pass", "issues": []})
            return super().__call__(req)

    script = Critical()
    gen, _ = await run(pool, script, settings(animator_enable_llm_critic=True))
    s1 = gen.scenes[0]
    assert s1.status == "ready" and len(s1.attempts) == 2 and s1.attempts[0].critic is not None
    fix = next(r for r in script.calls if r.label == "scene 1 attempt 2")
    assert "the tank is tiny -> make it bigger" in fix.messages[0].parts[-1].text  # type: ignore[union-attr]


async def test_paced_generation_waits_for_the_viewer(pool: EngineWorkerPool) -> None:
    s = settings(animator_buffer_target_seconds=5, animator_min_ready_scenes=1)
    script = Script()
    runner = GenerationRunner(MemoryStore(), Pipeline(EngineClient(pool), FakeLLM(script), s), s)
    gen = await runner.start(GenerationRequest(topic="Archimedes' principle", paced=True))
    runner.playhead(gen.id, 0.0)  # a viewer is watching from the start
    for _ in range(400):
        g = await runner.get(gen.id)
        if g and g.scenes and g.scenes[0].status == "ready":
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(1.0)
    assert stages_for(script, 1) == []  # 21 s buffered ahead of the viewer: waiting
    runner.playhead(gen.id, 60.0)
    await asyncio.wait_for(runner._tasks[gen.id], timeout=120)
    assert (await runner.get(gen.id)).status == "complete"  # type: ignore[union-attr]


# ---------------------------------------------------------------------- engine failures


class CrashingEngine(EngineClient):
    """The worker crashes (after the pool's own restart-and-retry) on the model's version of one
    scene (its simplified fallback compiles), or on everything with crash_on="*"."""

    def __init__(self, pool: EngineWorkerPool, crash_on: str) -> None:
        super().__init__(pool)
        self.crash_on = crash_on

    async def compile(self, scene: Any, prev_state: Any = None) -> Any:
        fallback = any(str(o.get("id", "")).startswith("flag_") for o in scene.get("objects", []))
        if self.crash_on == "*" or (scene.get("id") == self.crash_on and not fallback):
            raise WorkerCrashed("worker exited with code 3221225477")
        return await super().compile(scene, prev_state)


async def test_a_scene_that_keeps_crashing_the_engine_is_degraded(pool: EngineWorkerPool) -> None:
    script = Script()
    s = settings()
    runner = GenerationRunner(
        MemoryStore(), Pipeline(CrashingEngine(pool, "s02_scene_2"), FakeLLM(script), s), s
    )
    gen = await runner.start(GenerationRequest(topic="Archimedes' principle"))
    await asyncio.wait_for(runner._tasks[gen.id], timeout=120)
    done = await runner.get(gen.id)
    assert done is not None and done.status == "complete"
    s2 = done.scenes[1]
    assert s2.status == "degraded" and "engine failed" in (s2.degraded_reason or "")
    assert len(stages_for(script, 1)) == 1  # no LLM corrections: the engine, not the scene text, failed


async def test_an_engine_outage_pauses_the_generation(pool: EngineWorkerPool) -> None:
    script = Script()
    s = settings()
    runner = GenerationRunner(MemoryStore(), Pipeline(CrashingEngine(pool, "*"), FakeLLM(script), s), s)
    gen = await runner.start(GenerationRequest(topic="Archimedes' principle"))
    await asyncio.wait_for(runner._tasks[gen.id], timeout=120)
    done = await runner.get(gen.id)
    assert done is not None and done.status == "paused" and "resume" in (done.error or "")


# ---------------------------------------------------------------------- chunks (long form)


async def test_a_chapter_whose_storyboard_fails_gets_an_outline_scene(pool: EngineWorkerPool) -> None:
    class Refusing(Script):
        def __call__(self, req: LLMRequest) -> Any:
            if req.stage == "storyboard" and "chapter now: c2" in req.messages[0].parts[0].text:  # type: ignore[union-attr]
                self.calls.append(req)
                return LLMRefusal("model refused: no")
            return super().__call__(req)

    script = Refusing()
    gen, _ = await run(pool, script)
    assert gen.status == "complete" and gen.metrics.storyboard_fallbacks == 1
    assert [s.chapter_id for s in gen.scenes] == ["c1", "c1", "c2"]
    assert gen.scenes[2].title == "Chapter 2" and gen.scenes[2].brief.transition == "fade"
    assert gen.scenes[2].status in ("ready", "degraded")
    assert sum(1 for r in script.calls if r.stage == "storyboard") == 1 + 3  # c1, then c2 tried three times


async def test_the_first_chapter_plays_while_the_next_is_still_being_storyboarded(
    pool: EngineWorkerPool,
) -> None:
    release = asyncio.Event()

    class SlowBoard(Script):
        async def slow(self, req: LLMRequest) -> Any:
            await release.wait()
            return Script.__call__(self, req)

        def __call__(self, req: LLMRequest) -> Any:
            if req.stage == "storyboard" and "chapter now: c2" in req.messages[0].parts[0].text:  # type: ignore[union-attr]
                return self.slow(req)
            return super().__call__(req)

    s = settings()
    runner = GenerationRunner(MemoryStore(), Pipeline(EngineClient(pool), FakeLLM(SlowBoard()), s), s)
    gen = await runner.start(GenerationRequest(topic="Bernoulli's theorem", duration_min=15))
    for _ in range(400):
        g = await runner.get(gen.id)
        if g and len(g.scenes) >= 2 and g.scenes[1].status == "ready":
            break
        await asyncio.sleep(0.05)
    g = await runner.get(gen.id)
    assert g is not None and [s.status for s in g.scenes] == ["ready", "ready"]
    assert g.chapters_done == ["c1"] and g.metrics.first_play_s is not None  # playable, c2 still coming
    release.set()
    await asyncio.wait_for(runner._tasks[gen.id], timeout=120)
    done = await runner.get(gen.id)
    assert done is not None and done.status == "complete" and len(done.scenes) == 4


def test_short_lessons_are_one_chapter() -> None:
    from animator.pipeline.models import EducationalPlan
    from animator.pipeline.stages import one_chapter_if_short

    p = EducationalPlan.model_validate(plan(3))
    merged = one_chapter_if_short(p, 30)
    assert len(merged.chapters) == 1 and merged.chapters[0].target_duration_s == 120.0
    assert merged.chapters[0].key_ideas == ["idea", "idea", "idea"]
    assert len(one_chapter_if_short(p, 180).chapters) == 3


async def test_a_30_second_lesson_needs_one_storyboard_call(pool: EngineWorkerPool) -> None:
    script = Script()
    gen, _ = await run(pool, script, duration_min=0.5)
    assert gen.status == "complete" and gen.chapters_done == ["c1"]
    assert sum(1 for r in script.calls if r.stage == "storyboard") == 1


# ---------------------------------------------------------------------- parallel mode (fast)


def parallel(**kw: Any) -> Settings:
    return settings(animator_scene_mode="parallel", **kw)


def standalone(i: int, attempt: int, req: LLMRequest) -> Any:
    """Parallel scenes start on an empty stage: the opening Archimedes scene stands on its own."""
    return copy.deepcopy(SCENES[0])


def text_of(req: LLMRequest) -> str:
    return "\n".join(p.text for m in req.messages for p in m.parts if hasattr(p, "text"))


async def test_parallel_mode_plans_a_short_lesson_in_one_call_and_draws_its_scenes_at_once(
    pool: EngineWorkerPool,
) -> None:
    both = asyncio.Event()
    started: list[int] = []

    async def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        started.append(i)
        if len(started) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), timeout=10)  # hangs (and fails) if scenes ran one by one
        return standalone(i, attempt, req)

    script = Script(scene)
    gen, _ = await run(pool, script, parallel(), duration_min=0.5)
    assert gen.status == "complete", gen.error
    assert [r.label for r in script.calls][:1] == ["outline"]  # no separate plan or storyboard call
    assert not any(r.stage == "educational_planner" for r in script.calls)
    assert sorted(started) == [0, 1] and [s.status for s in gen.scenes] == ["ready", "ready"]
    assert (
        gen.plan is not None and len(gen.plan.chapters) == 1 and gen.plan.chapters[0].target_duration_s == 30
    )
    assert gen.metrics.first_play_s is not None
    for r in script.calls[1:]:
        assert "The stage starts empty" in text_of(r)
    second = gen.scenes[1].dsl
    assert second is not None and second["transition_in"]["type"] in ("fade", "cut", "wipe")
    assert second["camera"]["zoom"] == 1


async def test_parallel_mode_storyboards_every_chapter_of_a_long_lesson_at_once(
    pool: EngineWorkerPool,
) -> None:
    both = asyncio.Event()
    boards: list[str] = []

    class Boards(Script):
        async def board(self, req: LLMRequest) -> Any:
            boards.append(req.label)
            if len(boards) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), timeout=10)
            return Script.__call__(self, req)

        def __call__(self, req: LLMRequest) -> Any:
            if req.stage == "storyboard" and req.label != "outline" and req.label not in boards:
                return self.board(req)
            return super().__call__(req)

    script = Boards(standalone)
    gen, _ = await run(pool, script, parallel(), duration_min=15)
    assert gen.status == "complete", gen.error
    assert sorted(boards) == ["storyboard c1", "storyboard c2"]
    assert [(s.index, s.chapter_id) for s in gen.scenes] == [(0, "c1"), (1, "c1"), (2, "c2"), (3, "c2")]
    assert [s.id[:3] for s in gen.scenes] == ["s01", "s02", "s03", "s04"]
    assert all(s.status == "ready" for s in gen.scenes)


async def test_parallel_mode_falls_back_to_plan_and_storyboard_when_the_outline_fails(
    pool: EngineWorkerPool,
) -> None:
    class NoOutline(Script):
        def __call__(self, req: LLMRequest) -> Any:
            if req.label == "outline":
                self.calls.append(req)
                return LLMRefusal("model refused: no")
            return super().__call__(req)

    script = NoOutline(standalone)
    gen, _ = await run(pool, script, parallel(), duration_min=0.5)
    assert gen.status == "complete", gen.error
    stages = [r.stage for r in script.calls if not r.label.startswith("scene")]
    assert stages == ["storyboard"] * 3 + ["educational_planner", "storyboard"]


async def test_parallel_mode_a_fatal_error_stops_the_other_scenes(pool: EngineWorkerPool) -> None:
    never = asyncio.Event()

    async def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        if i == 1:
            return LLMAuthError("OpenAI rejected the API key")
        await never.wait()
        return standalone(i, attempt, req)

    gen, runner = await run(pool, Script(scene), parallel(), duration_min=0.5)
    assert gen.status == "failed" and "API key" in (gen.error or "")
    assert [s.status for s in gen.scenes] == ["planned", "planned"]
    assert not runner.running(gen.id)


async def test_parallel_mode_an_outage_pauses_and_resume_finishes_the_rest(pool: EngineWorkerPool) -> None:
    outage = {"on": True}
    first_ready = asyncio.Event()

    async def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        if i == 1 and outage["on"]:
            await asyncio.wait_for(first_ready.wait(), timeout=10)
            return LLMUnavailable("OpenAI call failed after 4 retries: 503")
        return standalone(i, attempt, req)

    script = Script(scene)
    s = parallel()
    store = MemoryStore()
    runner = GenerationRunner(store, Pipeline(EngineClient(pool), FakeLLM(script), s), s)
    gen = await runner.start(GenerationRequest(topic="Archimedes' principle", duration_min=0.5))
    for _ in range(400):
        g = await runner.get(gen.id)
        if g and g.scenes and g.scenes[0].status == "ready":
            first_ready.set()
            break
        await asyncio.sleep(0.02)
    await asyncio.wait_for(runner._tasks[gen.id], timeout=60)
    paused = await runner.get(gen.id)
    assert paused is not None and paused.status == "paused"
    assert [x.status for x in paused.scenes] == ["ready", "planned"]
    outage["on"] = False
    await runner.resume(gen.id)
    await asyncio.wait_for(runner._tasks[gen.id], timeout=60)
    done = await runner.get(gen.id)
    assert done is not None and done.status == "complete"
    assert len(stages_for(script, 0)) == 1  # scene 1 was not regenerated
    assert sum(1 for r in script.calls if r.label == "outline") == 1  # nor was the outline


def test_parallel_scenes_start_on_a_clean_stage() -> None:
    from animator.pipeline.generator import normalize

    gen = Generation(request=GenerationRequest(topic="Archimedes"))
    board = StoryboardChapter(
        chapter_id="c1", scenes=[StoryboardScene(**brief("A")), StoryboardScene(**brief("B"))]
    )
    first, second = scene_records(gen, board)
    dsl = {"objects": [], "transition_in": {"type": "continue"}}
    opening = normalize(dsl, first, independent=True)
    assert "camera" in opening and opening["transition_in"]["fresh"] is True
    out = normalize(dsl, second, independent=True)
    assert out["transition_in"] == {"type": "fade", "duration": 0.5, "fresh": True, "keep": None}
    assert out["camera"]["zoom"] == 1
    kept = normalize({"transition_in": {"type": "wipe"}, "camera": {"zoom": 2}}, second, independent=True)
    assert kept["transition_in"] == {"type": "wipe", "fresh": True, "keep": None} and kept["camera"] == {
        "zoom": 2
    }


async def test_parallel_scenes_may_reuse_an_id_for_a_different_object(pool: EngineWorkerPool) -> None:
    """Scenes drawn on their own do not share identities, in the pipeline or in a lesson chain."""
    from animator.pipeline.generator import normalize

    gen = Generation(request=GenerationRequest(topic="Archimedes"))
    board = StoryboardChapter(
        chapter_id="c1", scenes=[StoryboardScene(**brief("A")), StoryboardScene(**brief("B"))]
    )
    first, second = scene_records(gen, board)
    a = normalize(copy.deepcopy(SCENES[0]), first, independent=True)
    b = copy.deepcopy(SCENES[0])
    reused = next(o for o in b["objects"] if o.get("type") not in ("text", "ball"))
    old_id = reused["id"]
    b["objects"] = [o for o in b["objects"] if o["id"] != old_id] + [
        {"id": old_id, "type": "ball", "params": {}, "place": {"x": 800, "y": 450}}
    ]
    b["timeline"] = [x for x in b.get("timeline") or [] if old_id not in json.dumps(x)]
    b = normalize(b, second, independent=True)
    engine = EngineClient(pool)
    first_res = await engine.compile(a, None)
    assert first_res.ok and first_res.endState is not None
    chained = await engine.compile(b, first_res.endState)  # as the browser plays them
    assert not any(i.code == "IDENTITY_TYPE_CHANGE" for i in chained.issues)
