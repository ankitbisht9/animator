"""Streaming outline: briefs are used while the reply is still arriving."""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

from animator.engine_client import EngineClient, EngineWorkerPool
from animator.jobs.runner import GenerationRunner
from animator.jobs.store import MemoryStore
from animator.llm import CallContext, FakeLLM, LLMRequest, LLMResult, Usage
from animator.pipeline.generator import Pipeline
from animator.pipeline.models import EducationalPlan, GenerationRequest, StoryboardScene
from animator.pipeline.stages import OutlineSink
from animator.pipeline.streaming import JsonScanner
from tests.test_pipeline import SCENES, Script, brief, outline_plan, settings


def scan(text: str, chunk: int) -> list[tuple[str, Any]]:
    got: list[tuple[str, Any]] = []
    s = JsonScanner(lambda k, v: got.append((k, v)), lambda k, i, v: got.append((f"{k}[{i}]", v)))
    for i in range(0, len(text), chunk):
        s.feed(text[i : i + chunk])
    return got


def test_the_scanner_reports_values_and_array_items_as_they_complete() -> None:
    doc: dict[str, Any] = {
        "plan": {"title": 'Braces } ] and "quotes" \\ in strings', "n": [1, {"a": "{"}]},
        "count": 3,
        "scenes": [{"t": "one", "x": [1, 2]}, {"t": "two ]}"}, {"t": "three"}],
    }
    text = json.dumps(doc)
    for chunk in (1, 7, len(text)):
        got = scan(text, chunk)
        assert got == [
            ("plan", doc["plan"]),
            ("scenes[0]", doc["scenes"][0]),
            ("scenes[1]", doc["scenes"][1]),
            ("scenes[2]", doc["scenes"][2]),
            ("scenes", doc["scenes"]),
        ]


def test_the_scanner_starts_over_on_reset() -> None:
    got: list[str] = []
    s = JsonScanner(lambda k, v: got.append(k), lambda k, i, v: got.append(f"{k}[{i}]"))
    s.feed('{"scenes": [{"a": 1}, {"b"')
    s.reset()
    s.feed('{"scenes": [{"c": 3}]}')
    assert got == ["scenes[0]", "scenes[0]", "scenes"]


def test_the_outline_sink_hands_over_briefs_in_order_and_keeps_them_across_retries() -> None:
    plans: list[EducationalPlan] = []
    briefs: list[StoryboardScene] = []
    sink = OutlineSink(lambda p: p.to_plan("anyone", 30, []), plans.append, briefs.append)
    bad = brief("Two")
    bad["duration_s"] = "long"
    first = json.dumps({"plan": outline_plan(), "scenes": [brief("One"), bad, brief("Three")]})
    sink.reset()
    sink.feed(first)
    assert [b.title for b in briefs] == ["One"]  # "Two" is invalid, so "Three" waits too
    sink.reset()  # a retry
    sink.feed(json.dumps({"plan": outline_plan(), "scenes": [brief("Uno"), brief("Dos"), brief("Tres")]}))
    assert len(plans) == 1 and [b.title for b in briefs] == ["One", "Dos", "Tres"]


class SlowOutline:
    """Streams the outline reply in pieces, pausing in the middle until a scene has started."""

    def __init__(self, script: Script, scene_started: asyncio.Event) -> None:
        self.fake = FakeLLM(script)
        self.script = script
        self.scene_started = scene_started
        self.outline_done_at: float | None = None
        self.first_scene_at: float | None = None

    async def generate(self, request: LLMRequest, ctx: CallContext | None = None) -> LLMResult:
        ctx = ctx or CallContext()
        loop = asyncio.get_running_loop()
        if request.label != "outline":
            if self.first_scene_at is None:
                self.first_scene_at = loop.time()
                self.scene_started.set()
            return await self.fake.generate(request, ctx)
        out = self.script(request)
        text = json.dumps(out)
        assert ctx.stream is not None
        ctx.stream.reset()
        half = text.index('"scenes"') + len(json.dumps(out["scenes"][0])) + 20
        ctx.stream.feed(text[:half])
        await asyncio.wait_for(self.scene_started.wait(), timeout=10)  # scene 1 starts mid-outline
        ctx.stream.feed(text[half:])
        self.outline_done_at = loop.time()
        return LLMResult(json.loads(text), "fake", Usage(1000, 0, 500), 0.0, 0.0, 0, ["fake"])


async def test_scenes_start_while_the_outline_is_still_streaming(pool: EngineWorkerPool) -> None:
    script = Script(lambda i, a, r: copy.deepcopy(SCENES[0]))
    llm = SlowOutline(script, asyncio.Event())
    s = settings(animator_scene_mode="parallel")
    runner = GenerationRunner(MemoryStore(), Pipeline(EngineClient(pool), llm, s), s)
    gen = await runner.start(GenerationRequest(topic="Archimedes' principle", duration_min=0.5))
    await asyncio.wait_for(runner._tasks[gen.id], timeout=60)
    done = await runner.get(gen.id)
    assert done is not None and done.status == "complete", done and done.error
    assert llm.first_scene_at is not None and llm.outline_done_at is not None
    assert llm.first_scene_at < llm.outline_done_at
    assert [x.status for x in done.scenes] == ["ready", "ready"]
    assert [x.id[:3] for x in done.scenes] == ["s01", "s02"] and done.chapters_done == ["c1"]
