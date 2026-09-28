"""asyncio generation runner: plan -> per-chapter storyboard (prefetched one chapter ahead) ->
scenes in order, each from the previous scene's engine-computed end state.

- State is persisted after every step, so a restarted server resumes where it stopped (scenes
  already READY are never regenerated).
- Progress is published to subscribers (the SSE endpoint) as it happens.
- Optional pacing (`request.paced`): generation stays `buffer_target_seconds` ahead of the
  viewer's playhead and waits there (spec Section 13).
- Cancellation, budget hard stops and provider outages end in explicit states; outages and
  engine faults are `paused` and resumable.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from animator.config import STAGES, Settings
from animator.engine_client.errors import EngineError
from animator.jobs.store import GenerationStore
from animator.llm import (
    Budget,
    BudgetExceeded,
    CallContext,
    CallRecord,
    LLMAuthError,
    LLMBadOutput,
    LLMError,
    LLMIncomplete,
    LLMRefusal,
)
from animator.pipeline.generator import Pipeline, chapter_outline, scene_records
from animator.pipeline.models import TERMINAL, Generation, GenerationRequest, StoryboardChapter

log = logging.getLogger("animator.runner")


class Hub:
    """In-process pub/sub of generation events (one queue per SSE subscriber)."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}

    def publish(self, gen_id: str, event: dict[str, Any]) -> None:
        for q in list(self._subs.get(gen_id, ())):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    @contextlib.asynccontextmanager
    async def subscribe(self, gen_id: str) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subs.setdefault(gen_id, set()).add(q)
        try:
            yield q
        finally:
            self._subs.get(gen_id, set()).discard(q)


def scene_event(gen: Generation, index: int) -> dict[str, Any]:
    s = gen.scenes[index]
    return {
        "type": "scene",
        "index": index,
        "id": s.id,
        "status": s.status,
        "duration": s.duration,
        "dsl": s.dsl,
    }


class GenerationRunner:
    def __init__(self, store: GenerationStore, pipeline: Pipeline, settings: Settings) -> None:
        self.store = store
        self.pipeline = pipeline
        self.settings = settings
        self.hub = Hub()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._live: dict[str, Generation] = {}
        self._pending_calls: dict[str, list[dict[str, Any]]] = {}
        self._playhead_seen: dict[str, float] = {}
        self._wake: dict[str, asyncio.Event] = {}
        self._sem = asyncio.Semaphore(settings.animator_generation_concurrency)
        self._cancelled: set[str] = set()
        self._closing = False

    # ------------------------------------------------------------------ control

    async def start(self, request: GenerationRequest) -> Generation:
        gen = Generation(request=request)
        gen.models = {s: self._models_of(s) for s in STAGES}
        await self._save(gen)
        self._spawn(gen)
        return gen

    def _models_of(self, stage: str) -> list[str]:
        try:
            return self.settings.stage_model(stage)[0]
        except ValueError:
            return []

    async def get(self, gen_id: str) -> Generation | None:
        return self._live.get(gen_id) or await self.store.get(gen_id)

    def running(self, gen_id: str) -> bool:
        task = self._tasks.get(gen_id)
        return task is not None and not task.done()

    async def resume(self, gen_id: str) -> Generation | None:
        gen = await self.get(gen_id)
        if gen is None or gen.status in ("complete", "cancelled") or self.running(gen_id):
            return gen
        gen.error = None
        gen.status = "queued"
        await self._save(gen)
        self._spawn(gen)
        return gen

    async def cancel(self, gen_id: str) -> Generation | None:
        gen = await self.get(gen_id)
        if gen is None:
            return None
        task = self._tasks.get(gen_id)
        if task is not None and not task.done():
            self._cancelled.add(gen_id)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        elif gen.status not in TERMINAL:
            gen.status = "cancelled"
            await self._save(gen)
        return await self.get(gen_id)

    def playhead(self, gen_id: str, t: float) -> None:
        gen = self._live.get(gen_id)
        if gen is not None:
            gen.playhead = max(0.0, t)
        self._playhead_seen[gen_id] = time.monotonic()
        wake = self._wake.get(gen_id)
        if wake is not None:
            wake.set()

    async def resume_all(self) -> list[str]:
        """After a restart: continue every generation that was still in progress."""
        resumed = []
        for gen in await self.store.recent(limit=200):
            if gen.status in ("queued", "planning", "generating"):
                self._spawn(gen)
                resumed.append(gen.id)
        return resumed

    async def close(self) -> None:
        """Shutdown: in-progress generations are saved as `paused` and resume on next start."""
        self._closing = True
        tasks = [t for t in self._tasks.values() if not t.done()]
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    # ------------------------------------------------------------------ internals

    def _spawn(self, gen: Generation) -> None:
        self._live[gen.id] = gen
        self._tasks[gen.id] = asyncio.create_task(self._run(gen), name=f"generation-{gen.id}")

    async def _save(self, gen: Generation) -> None:
        gen.updated_at = time.time()
        gen.version += 1
        await self.store.save(gen)
        calls = self._pending_calls.pop(gen.id, [])
        if calls:
            await self.store.append_calls(gen.id, calls)
        self.hub.publish(gen.id, {"type": "update", "generation": gen.summary()})

    def _on_call(self, gen: Generation, record: CallRecord) -> None:
        m = gen.metrics
        m.llm_calls += 1
        m.cost_usd = round(m.cost_usd + (record.cost_usd or 0.0), 6)
        m.input_tokens += record.usage.input_tokens
        m.cached_input_tokens += record.usage.cached_input_tokens
        m.output_tokens += record.usage.output_tokens
        m.reasoning_tokens += record.usage.reasoning_tokens
        self._pending_calls.setdefault(gen.id, []).append(record.to_json())

    async def _run(self, gen: Generation) -> None:
        prefetch: dict[str, asyncio.Task[StoryboardChapter]] = {}
        try:
            async with self._sem:
                await self._generate(gen, prefetch)
        except asyncio.CancelledError:
            if gen.id in self._cancelled:
                gen.status = "cancelled"
            elif self._closing:
                gen.status = "paused"
                gen.error = "server stopped; resumes on next start"
            self._reset_in_flight(gen)
            await self._save(gen)
            if not self._closing:
                raise
        except (LLMAuthError, BudgetExceeded) as e:
            gen.status = "failed"
            gen.error = str(e)
            self._reset_in_flight(gen)
            await self._save(gen)
        except (LLMError, EngineError) as e:
            gen.status = "paused"
            gen.error = f"{e} (resume to continue)"
            self._reset_in_flight(gen)
            await self._save(gen)
        except Exception as e:
            log.exception("generation %s failed", gen.id)
            gen.status = "failed"
            gen.error = f"internal error: {type(e).__name__}: {e}"
            self._reset_in_flight(gen)
            await self._save(gen)
        finally:
            for t in prefetch.values():
                t.cancel()
            self._cancelled.discard(gen.id)
            self._wake.pop(gen.id, None)

    def _reset_in_flight(self, gen: Generation) -> None:
        for s in gen.scenes:
            if s.status in ("generating", "validating", "correcting"):
                s.status = "planned"

    def _ctx(self, gen: Generation) -> CallContext:
        budget = Budget(
            self.settings.llm_budget_usd_per_generation, self.settings.llm_budget_tokens_per_generation
        )
        budget.spent_usd = gen.metrics.cost_usd  # the budget spans resumes
        budget.spent_tokens = gen.metrics.input_tokens + gen.metrics.output_tokens
        return CallContext(budget=budget, on_record=lambda r: self._on_call(gen, r))

    async def _generate(self, gen: Generation, prefetch: dict[str, asyncio.Task[StoryboardChapter]]) -> None:
        ctx = self._ctx(gen)
        stages = await self.pipeline.stages()
        self._reset_in_flight(gen)

        async def changed() -> None:
            await self._save(gen)

        if gen.plan is None:
            gen.status = "planning"
            await self._save(gen)
            gen.plan = await stages.plan(gen, ctx)
        gen.status = "generating"
        gen.error = None
        await self._save(gen)

        chapters = gen.plan.chapters
        for ci, chapter in enumerate(chapters):
            if chapter.id not in gen.chapters_done:
                task = prefetch.pop(chapter.id, None)
                try:
                    board = await task if task is not None else await stages.storyboard(gen, chapter.id, ctx)
                except (LLMRefusal, LLMIncomplete, LLMBadOutput) as e:
                    # A chapter whose storyboard keeps failing still gets a scene, planned straight
                    # from the lesson plan; later chapters continue (spec Section 17).
                    log.warning(
                        "storyboard for %s/%s failed (%s): using the plan outline", gen.id, chapter.id, e
                    )
                    board = chapter_outline(chapter)
                    gen.metrics.storyboard_fallbacks += 1
                gen.scenes.extend(scene_records(gen, board))
                gen.chapters_done.append(chapter.id)
                await self._save(gen)
            nxt = chapters[ci + 1] if ci + 1 < len(chapters) else None
            if nxt is not None and nxt.id not in gen.chapters_done and nxt.id not in prefetch:
                # Stage 2 for the next chapter runs while Stage 3 works on this one (spec Section 13).
                prefetch[nxt.id] = asyncio.create_task(self._storyboard_ahead(gen, nxt.id, ctx))
            for rec in [s for s in gen.scenes if s.chapter_id == chapter.id]:
                if rec.done:
                    continue
                await self._pace(gen)
                prev = gen.scenes[rec.index - 1] if rec.index > 0 else None
                previous = [s.dsl for s in gen.scenes[: rec.index] if s.dsl is not None]
                started = time.perf_counter()
                await self.pipeline.run_scene(gen, rec, prev, ctx, changed, previous)
                took = time.perf_counter() - started
                if rec.playable and gen.metrics.first_play_s is None:
                    gen.metrics.first_play_s = round(time.time() - gen.created_at, 1)
                if rec.duration and took > rec.duration:
                    gen.metrics.slow_scenes.append(f"{rec.id}: {took:.0f}s to generate {rec.duration:.0f}s")
                self.hub.publish(gen.id, scene_event(gen, rec.index))
                await self._save(gen)

        gen.status = "complete"
        gen.metrics.generation_s = round(time.time() - gen.created_at, 1)
        await self._save(gen)

    async def _storyboard_ahead(
        self, gen: Generation, chapter_id: str, ctx: CallContext
    ) -> StoryboardChapter:
        stages = await self.pipeline.stages()
        return await stages.storyboard(gen, chapter_id, ctx)

    async def _pace(self, gen: Generation) -> None:
        """With pacing on and a viewer present, wait while enough is buffered ahead of them."""
        if not gen.request.paced:
            return
        target = self.settings.animator_buffer_target_seconds
        wake = self._wake.setdefault(gen.id, asyncio.Event())
        while True:
            seen = self._playhead_seen.get(gen.id)
            if seen is None or time.monotonic() - seen > 120:
                return  # nobody watching: keep generating
            start = 0.0
            ahead_scenes = 0
            for s in gen.scenes:
                if not s.playable:
                    break
                dur = s.duration or 0.0
                if start + dur > gen.playhead:
                    ahead_scenes += 1
                start += dur
            if start - gen.playhead < target or ahead_scenes < self.settings.animator_min_ready_scenes:
                return
            wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=5)
