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
from collections.abc import AsyncIterator, Callable
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
from animator.pipeline.generator import Pipeline, chapter_outline, scene_record, scene_records
from animator.pipeline.models import (
    TERMINAL,
    Chapter,
    DirectedScene,
    EducationalPlan,
    Generation,
    GenerationRequest,
    SceneRecord,
    StoryboardChapter,
    StoryboardScene,
)
from animator.pipeline.stages import Stages, script_brief, script_plan, script_words, target_words

log = logging.getLogger("animator.runner")


def _first_error(eg: BaseExceptionGroup[BaseException]) -> BaseException:
    """The failure that ended a task group (not the cancellations it caused)."""
    for e in eg.exceptions:
        if isinstance(e, BaseExceptionGroup):
            return _first_error(e)
        if not isinstance(e, asyncio.CancelledError):
            return e
    return eg.exceptions[0]


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


def scene_event(gen: Generation, index: int, speaks: bool = False) -> dict[str, Any]:
    s = gen.scenes[index]
    return {
        "type": "scene",
        "index": index,
        "id": s.id,
        "status": s.status,
        "duration": s.duration,
        "dsl": s.dsl,
        "narration": s.narration if speaks else None,
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
        self._save_locks: dict[str, asyncio.Lock] = {}
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

    def busy(self) -> bool:
        """Is any lesson being made now? (Background work such as the object harvest waits.)"""
        return any(not t.done() for t in self._tasks.values())

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
        # Scenes generated in parallel finish at the same time; saves of one generation take turns.
        async with self._save_locks.setdefault(gen.id, asyncio.Lock()):
            gen.updated_at = time.time()
            gen.version += 1
            # The lesson lives in memory while it runs; a file that stays locked (the store already
            # retried) only delays persistence to the next save, it must not fail the lesson.
            try:
                await self.store.save(gen)
            except OSError as e:
                log.warning("saving %s failed (%s): it is saved again on the next change", gen.id, e)
            calls = self._pending_calls.pop(gen.id, [])
            if calls:
                try:
                    await self.store.append_calls(gen.id, calls)
                except OSError as e:
                    log.warning("writing the call log of %s failed (%s): kept for the next save", gen.id, e)
                    self._pending_calls.setdefault(gen.id, [])[:0] = calls
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
            self.pipeline.abandon(gen.id)
            for t in prefetch.values():
                t.cancel()
            self._cancelled.discard(gen.id)
            self._wake.pop(gen.id, None)
            # The run is over and every call is counted (drafts that lost the race included): the
            # last event of a live stream, so viewers see the final cost.
            self.hub.publish(gen.id, {"type": "update", "generation": gen.summary(), "final": True})

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
        if self.settings.animator_scene_mode == "parallel":
            await self._generate_parallel(gen, stages, ctx, prefetch)
        else:
            if gen.plan is None:
                gen.status = "planning"
                await self._save(gen)
                gen.plan = await stages.plan(gen, ctx)
            await self._generating(gen)
            await self._generate_sequential(gen, stages, ctx, prefetch)
        gen.status = "complete"
        gen.metrics.generation_s = round(time.time() - gen.created_at, 1)
        await self._save(gen)
        # Scene drafts that lost the race are still finishing: count their cost, then save again.
        await self.pipeline.settle(gen.id)
        await self._save(gen)

    async def _generating(self, gen: Generation) -> None:
        gen.status = "generating"
        gen.error = None
        await self._save(gen)

    async def _board(
        self, gen: Generation, chapter: Chapter, stages: Stages, ctx: CallContext
    ) -> StoryboardChapter:
        try:
            return await stages.storyboard(gen, chapter.id, ctx)
        except (LLMRefusal, LLMIncomplete, LLMBadOutput) as e:
            # A chapter whose storyboard keeps failing still gets a scene, planned straight from the
            # lesson plan; later chapters continue (spec Section 17).
            log.warning("storyboard for %s/%s failed (%s): using the plan outline", gen.id, chapter.id, e)
            gen.metrics.storyboard_fallbacks += 1
            return chapter_outline(chapter)

    async def _add_chapter(self, gen: Generation, board: StoryboardChapter) -> None:
        gen.scenes.extend(scene_records(gen, board))
        gen.chapters_done.append(board.chapter_id)
        await self._save(gen)

    async def _scene(self, gen: Generation, rec: SceneRecord, ctx: CallContext, independent: bool) -> None:
        prev = gen.scenes[rec.index - 1] if rec.index > 0 and not independent else None
        previous = [] if independent else [s.dsl for s in gen.scenes[: rec.index] if s.dsl is not None]
        started = time.perf_counter()
        await self.pipeline.run_scene(gen, rec, prev, ctx, lambda: self._save(gen), previous)
        took = time.perf_counter() - started
        if rec.index == 0 and rec.playable and gen.metrics.first_play_s is None:
            gen.metrics.first_play_s = round(time.time() - gen.created_at, 1)
        if rec.duration and took > rec.duration:
            gen.metrics.slow_scenes.append(f"{rec.id}: {took:.0f}s to generate {rec.duration:.0f}s")
        self.hub.publish(gen.id, scene_event(gen, rec.index, self.pipeline.speech is not None))
        await self._save(gen)

    async def _generate_parallel(
        self,
        gen: Generation,
        stages: Stages,
        ctx: CallContext,
        prefetch: dict[str, asyncio.Task[StoryboardChapter]],
    ) -> None:
        """Every scene starts on a clean stage, so scenes are generated at once, each as soon as its
        brief exists: a short lesson's outline streams in and each scene starts the moment its brief
        is complete; a long lesson storyboards all its chapters at once."""
        sem = asyncio.Semaphore(self.settings.animator_scene_concurrency)
        launched: set[int] = set()

        async def one(rec: SceneRecord) -> None:
            async with sem:
                await self._scene(gen, rec, ctx, independent=True)

        try:
            async with asyncio.TaskGroup() as tg:

                def launch(rec: SceneRecord) -> None:
                    if rec.index not in launched and not rec.done:
                        launched.add(rec.index)
                        tg.create_task(one(rec))

                if gen.plan is not None and not gen.chapters_done and gen.scenes:
                    # Stopped while the outline was still streaming: start the lesson over.
                    gen.plan, gen.scenes, gen.teaching = None, [], None
                if gen.plan is None:
                    gen.status = "planning"
                    await self._save(gen)
                    gen.plan = await self._plan_parallel(gen, stages, ctx, launch)
                await self._generating(gen)
                for c in gen.plan.chapters:
                    if c.id not in gen.chapters_done and c.id not in prefetch:
                        prefetch[c.id] = asyncio.create_task(self._board(gen, c, stages, ctx))
                for c in gen.plan.chapters:
                    if c.id not in gen.chapters_done:
                        await self._add_chapter(gen, await prefetch.pop(c.id))
                    for rec in [s for s in gen.scenes if s.chapter_id == c.id]:
                        await self._pace(gen)
                        launch(rec)
        except BaseExceptionGroup as eg:
            # One failure (auth, budget, outage) cancels the other scenes; report it by its own type.
            raise _first_error(eg) from None

    async def _plan_parallel(
        self, gen: Generation, stages: Stages, ctx: CallContext, launch: Callable[[SceneRecord], None]
    ) -> EducationalPlan:
        """The teaching pipeline (ADR-044, see `_teach`) when it is on. Otherwise short lessons: plan
        and storyboard in one streamed call, each scene launched as soon as its brief arrives; longer
        ones (or when the outline fails): the plan only."""
        if self._teaching(gen):
            try:
                return await self._teach(gen, stages, ctx, launch)
            except (LLMRefusal, LLMIncomplete, LLMBadOutput) as e:
                if gen.scenes:
                    raise  # scenes are already being drawn from the script
                log.warning("teaching pipeline for %s failed (%s): planning it the fast way", gen.id, e)
                gen.plan, gen.teaching = None, None
        if gen.request.duration_min * 60 > self.settings.animator_outline_max_duration_s:
            return await stages.plan(gen, ctx)

        def on_plan(plan: EducationalPlan) -> None:
            gen.plan = plan  # scenes read the lesson title from it
            gen.status = "generating"

        def on_scene(brief: StoryboardScene) -> None:
            assert gen.plan is not None
            rec = scene_record(gen, gen.plan.chapters[0].id, brief)
            gen.scenes.append(rec)
            launch(rec)

        try:
            plan, board = await stages.outline(gen, ctx, on_plan=on_plan, on_scene=on_scene)
        except (LLMRefusal, LLMIncomplete, LLMBadOutput) as e:
            log.warning("outline for %s failed (%s): planning in two steps", gen.id, e)
            gen.plan, gen.scenes = None, []
            return await stages.plan(gen, ctx)
        gen.plan = plan
        for brief in board.scenes[len(gen.scenes) :]:
            gen.scenes.append(scene_record(gen, board.chapter_id, brief))
        gen.chapters_done.append(board.chapter_id)
        return plan

    def _teaching(self, gen: Generation) -> bool:
        return (
            self.settings.animator_pipeline == "teaching"
            and gen.request.duration_min * 60 <= self.settings.animator_teaching_max_duration_s
        )

    async def _teach(
        self, gen: Generation, stages: Stages, ctx: CallContext, launch: Callable[[SceneRecord], None]
    ) -> EducationalPlan:
        """The teaching pipeline (ADR-044): a teacher writes the whole explanation (every spoken word
        and what is seen), a reviewer checks and improves it, then a visual director plans every
        scene's picture. The director's reply streams: each scene is launched as soon as its plan
        (and every earlier scene's) is in. A failed review keeps the teacher's script; a failed or
        incomplete direction draws the remaining scenes from the teacher's show notes."""
        duration = gen.request.duration_min * 60
        script = await stages.teach(gen, ctx)
        changes: list[str] = []
        if self.settings.animator_script_review:
            try:
                reviewed = await stages.review(gen, script, ctx)
            except (LLMRefusal, LLMIncomplete, LLMBadOutput) as e:
                log.warning("script review for %s failed (%s): keeping the teacher's script", gen.id, e)
            else:
                target = target_words(duration)
                if script_words(reviewed.script) <= target * 1.35 or script_words(
                    reviewed.script
                ) <= script_words(script):
                    script, changes = reviewed.script, reviewed.changes
                else:
                    # The lesson would run far longer than asked: the teacher's shorter script stays.
                    log.warning(
                        "script review for %s made it %d words (target %d): keeping the teacher's script",
                        gen.id,
                        script_words(reviewed.script),
                        target,
                    )
        plan = script_plan(script, gen.request.audience, duration)
        teaching: dict[str, Any] = {"script": script.model_dump(), "changes": changes, "look": None}
        gen.plan, gen.teaching = plan, teaching
        gen.status = "generating"
        await self._save(gen)
        chapter = plan.chapters[0].id
        planned: dict[int, DirectedScene] = {}

        def add(k: int) -> None:
            rec = scene_record(gen, chapter, script_brief(script.scenes[k], planned.get(k)))
            gen.scenes.append(rec)
            launch(rec)

        def on_look(look: str) -> None:
            teaching["look"] = look

        def on_scene(directed: DirectedScene) -> None:
            k = directed.scene - 1
            if not 0 <= k < len(script.scenes) or k in planned:
                return
            planned[k] = directed
            while len(gen.scenes) < len(script.scenes) and len(gen.scenes) in planned:
                add(len(gen.scenes))

        try:
            direction = await stages.direct(
                gen, script, ctx, on_look, on_scene, library=self.pipeline.library_note(gen, script)
            )
            teaching["look"] = teaching["look"] or direction.look
            for directed in direction.scenes:
                on_scene(directed)
        except (LLMRefusal, LLMIncomplete, LLMBadOutput) as e:
            log.warning("visual direction for %s failed (%s): using the teacher's show notes", gen.id, e)
        while len(gen.scenes) < len(script.scenes):
            add(len(gen.scenes))
        gen.chapters_done.append(chapter)
        return plan

    async def _generate_sequential(
        self,
        gen: Generation,
        stages: Stages,
        ctx: CallContext,
        prefetch: dict[str, asyncio.Task[StoryboardChapter]],
    ) -> None:
        """Each scene continues from the previous scene's engine-computed end state."""
        assert gen.plan is not None
        chapters = gen.plan.chapters
        for ci, chapter in enumerate(chapters):
            if chapter.id not in gen.chapters_done:
                task = prefetch.pop(chapter.id, None)
                await self._add_chapter(gen, await (task or self._board(gen, chapter, stages, ctx)))
            nxt = chapters[ci + 1] if ci + 1 < len(chapters) else None
            if nxt is not None and nxt.id not in gen.chapters_done and nxt.id not in prefetch:
                # Stage 2 for the next chapter runs while Stage 3 works on this one (spec Section 13).
                prefetch[nxt.id] = asyncio.create_task(self._board(gen, nxt, stages, ctx))
            for rec in [s for s in gen.scenes if s.chapter_id == chapter.id]:
                if not rec.done:
                    await self._pace(gen)
                    await self._scene(gen, rec, ctx, independent=False)

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
