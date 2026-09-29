"""One scene through the loop, and the pieces the job runner sequences into a lesson.

    draft (animation planner)
      -> engine compile/validate (worker)
      -> errors?  correction with the structured issues (attempts 1..N-1: correction model,
                  final attempt: escalation model); an unusable reply gets a repair prompt
      -> ok       optional critics (text summary, or rendered keyframes to a vision model);
                  a "revise" verdict with major issues spends one more attempt
      -> READY, or after the last attempt a clearly flagged simplified scene (DEGRADED)

LLM refusals, truncation and malformed output count as failed attempts (spec Section 17).
Auth, outage and budget errors propagate: the generation stops and can be resumed.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from animator.config import Settings
from animator.engine_client import EngineClient
from animator.engine_client.errors import EngineError
from animator.llm import CallContext, LLMBadOutput, LLMClient, LLMIncomplete, LLMRefusal, LLMResult
from animator.pipeline.models import (
    Attempt,
    BeatPlan,
    Chapter,
    Generation,
    SceneRecord,
    StoryboardChapter,
    StoryboardScene,
)
from animator.pipeline.prompts import PromptPack, slug
from animator.pipeline.stages import Stages, format_issues
from animator.pipeline.tidy import autofix, tidy
from animator_contracts.protocol import CompileResult, SceneState

Changed = Callable[[], Awaitable[None]]


class KeyframeSource(Protocol):
    """Renders PNG keyframes of scene `index` of a lesson (for the visual critic)."""

    async def keyframes(
        self, scenes: list[dict[str, Any]], index: int, times: list[float]
    ) -> list[bytes]: ...


def scene_record(gen: Generation, chapter_id: str, brief: StoryboardScene, offset: int = 0) -> SceneRecord:
    """The record of the scene that follows the lesson's current scenes (plus `offset`)."""
    index = len(gen.scenes) + offset
    return SceneRecord(
        index=index,
        id=f"s{index + 1:02d}_{slug(brief.title)}",
        title=brief.title[:120],
        chapter_id=chapter_id,
        brief=brief,
    )


def scene_records(gen: Generation, board: StoryboardChapter) -> list[SceneRecord]:
    return [scene_record(gen, board.chapter_id, brief, i) for i, brief in enumerate(board.scenes)]


def chapter_outline(chapter: Chapter) -> StoryboardChapter:
    """A one-scene storyboard straight from the plan, for a chapter whose storyboard failed."""
    ideas = chapter.key_ideas[:4] or [chapter.goal]
    return StoryboardChapter(
        chapter_id=chapter.id,
        scenes=[
            StoryboardScene(
                title=chapter.title,
                purpose=chapter.goal,
                duration_s=max(12.0, min(40.0, chapter.target_duration_s)),
                layout="stage_with_caption",
                transition="fade",
                visual_plan=chapter.visual_approach,
                objects_new=[],
                objects_kept=[],
                objects_removed=[],
                physics=None,
                beats=[BeatPlan(narration=idea, on_screen=chapter.visual_approach) for idea in ideas],
                engagement="one clear, labelled picture with a highlight on the key idea",
                checkpoint=None,
            )
        ],
    )


def fallback_scene(rec: SceneRecord) -> dict[str, Any]:
    """A simple, valid, clearly flagged scene: title, the key idea, the narration as captions."""
    n = rec.index + 1
    beats = [{"id": f"b{i + 1}", "text": b.narration[:390]} for i, b in enumerate(rec.brief.beats[:12])]
    words = sum(len(b["text"].split()) for b in beats)
    duration = round(min(60.0, max(8.0, words / 2.3 + 3)), 1)
    title, note, flag = f"title_s{n:02d}", f"note_s{n:02d}", f"flag_s{n:02d}"
    return {
        "dsl_version": "1.0",
        "id": rec.id,
        "title": rec.title,
        "educational_purpose": rec.brief.purpose[:390],
        "duration": duration,
        "layout": "stage_with_caption",
        "transition_in": {"type": "fade", "duration": 0.6},
        "camera": dict(DEFAULT_CAMERA),
        "narration": {"beats": beats},
        "objects": [
            {
                "id": title,
                "type": "text",
                "params": {"text": rec.title[:80], "size": "heading", "weight": "bold"},
                "place": {"slot": "title_bar"},
                "layer": "overlay",
            },
            {
                "id": note,
                "type": "note",
                "params": {
                    "title": "Key idea",
                    "icon": "lightbulb",
                    "text": rec.brief.purpose[:300],
                    "width": 760,
                    "role": "accent",
                },
                "place": {"slot": "stage.center"},
            },
            {
                "id": flag,
                "type": "badge",
                "params": {"text": "Simplified scene", "role": "muted", "icon": "info"},
                "place": {"relativeTo": note, "side": "below", "gap": 40},
            },
        ],
        "timeline": [
            {"id": "t0", "action": "write", "target": title, "start": {"at": 0.2}},
            {"id": "n1", "action": "create", "target": note, "start": {"at": 0.8}},
            {"id": "f1", "action": "create", "target": flag, "start": {"after": "n1"}},
        ],
    }


DEFAULT_CAMERA = {"center": {"x": 800, "y": 450}, "zoom": 1}


def normalize(dsl: dict[str, Any], rec: SceneRecord, independent: bool = False) -> dict[str, Any]:
    """Pin the fields the pipeline owns (id, version); the rest is the model's.

    An independent scene (parallel mode) must not depend on the scene before it: it starts from a
    clean stage (a fade) and its own camera, so the browser, which plays scenes in a chain, draws
    exactly what the engine validated on its own."""
    dsl = dict(dsl)
    dsl["dsl_version"] = "1.0"
    dsl["id"] = rec.id
    if not dsl.get("title"):
        dsl["title"] = rec.title
    if independent:
        transition = dict(dsl.get("transition_in") or {"type": "continue"})
        if rec.index > 0 and transition.get("type") not in ("fade", "cut", "wipe"):
            transition = {"type": "fade", "duration": 0.5}
        # `fresh`: compiled without the previous scene's state everywhere (engine, player, export),
        # so ids an earlier scene used for something else do not clash.
        transition.update(fresh=True, keep=None)
        dsl["transition_in"] = transition
        if not dsl.get("camera"):
            dsl["camera"] = dict(DEFAULT_CAMERA)
    elif rec.brief.transition != "continue" and not dsl.get("transition_in"):
        dsl["transition_in"] = {"type": rec.brief.transition}
    return tidy(dsl)


def summarize_for_critic(dsl: dict[str, Any], res: CompileResult) -> str:
    objs = []
    for o in dsl.get("objects") or []:
        params = o.get("params") or {}
        text = next(
            (str(params[k]) for k in ("text", "label", "title", "tex") if isinstance(params.get(k), str)), ""
        )
        objs.append(f"- {o.get('id')} ({o.get('type')}){': ' + text[:80] if text else ''}")
    acts = [
        f"- {a.get('id')}: {a.get('action')} {a.get('target') or ''} start={a.get('start')}"
        for a in (dsl.get("timeline") or [])[:60]
    ]
    beats = [f"- {b.id} {b.start:.1f}-{b.end:.1f}s: {b.text}" for b in (res.beats or [])]
    warns = [f"- {i.code}: {i.message}" for i in res.issues if i.severity == "warning"][:20]
    return "\n".join(
        [
            f"Duration {res.duration}s.",
            "Narration:",
            *beats,
            "Objects:",
            *objs,
            "Timeline:",
            *acts,
            "Engine warnings:",
            *(warns or ["- none"]),
        ]
    )


Draft = tuple[LLMResult, dict[str, Any], CompileResult, bool]
"""A scene call's result, its shaped (and maybe auto-fixed) DSL, the engine's verdict, auto-fixed?"""


def _count(res: CompileResult) -> int:
    return sum(1 for i in res.issues if i.severity == "error")


def _keys(res: CompileResult) -> set[tuple[str, str]]:
    return {(i.code, i.path) for i in res.issues if i.severity == "error"}


def _errors(d: Draft) -> int:
    return _count(d[2])


def _consume(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


class Pipeline:
    def __init__(
        self,
        engine: EngineClient,
        llm: LLMClient,
        settings: Settings,
        keyframes: KeyframeSource | None = None,
    ) -> None:
        self.engine = engine
        self.llm = llm
        self.settings = settings
        self.keyframes = keyframes
        self._stages: Stages | None = None
        # Losing scene drafts still running, per generation (see `_drafts`).
        self._background: dict[str, set[asyncio.Task[Any]]] = {}

    async def stages(self) -> Stages:
        if self._stages is None:
            catalog = await self.engine.catalog()
            schema = await self.engine.schema("llm")
            self._stages = Stages(self.llm, self.settings, PromptPack.from_repo(catalog), schema)
        return self._stages

    # ------------------------------------------------------------------ one scene

    async def run_scene(
        self,
        gen: Generation,
        rec: SceneRecord,
        prev: SceneRecord | None,
        ctx: CallContext,
        changed: Changed,
        previous_scenes: list[dict[str, Any]],
    ) -> None:
        prev_state = SceneState.model_validate(prev.end_state) if prev and prev.end_state else None
        prev_summary = prev.state_summary if prev else None
        max_attempts = 1 + max(0, self.settings.animator_max_correction_attempts)
        rec.attempts = []
        rec.started_at = rec.started_at or time.time()
        rivals: set[asyncio.Task[Draft]] = set()  # drafts still running, racing the corrections
        try:
            await self._attempts(
                gen, rec, prev_summary, prev_state, ctx, changed, previous_scenes, max_attempts, rivals
            )
        finally:
            self._park(gen.id, rivals)

    async def _attempts(
        self,
        gen: Generation,
        rec: SceneRecord,
        prev_summary: str | None,
        prev_state: SceneState | None,
        ctx: CallContext,
        changed: Changed,
        previous_scenes: list[dict[str, Any]],
        max_attempts: int,
        rivals: set[asyncio.Task[Draft]],
    ) -> None:
        stages = await self.stages()
        dsl: dict[str, Any] | None = None
        issues: list[dict[str, Any]] = []
        llm_error: str | None = None
        critic_notes: str | None = None
        critics_used = False

        for n in range(1, max_attempts + 1):
            final = n == max_attempts and n > 1
            kind = "plan" if n == 1 else ("repair" if llm_error and dsl is None else "correction")
            stage = "animation_planner" if n == 1 else ("correction_final" if final else "correction")
            rec.status = "generating" if n == 1 else "correcting"
            await changed()
            t0 = time.perf_counter()
            attempt = Attempt(n=n, kind=kind, stage=stage)
            count = (
                self.settings.animator_scene_drafts if n == 1 else self.settings.animator_correction_drafts
            )
            mine = {
                asyncio.create_task(
                    self._draft(
                        stages.scene(
                            gen,
                            rec,
                            prev_summary,
                            ctx,
                            attempt=n,
                            final=final,
                            previous=dsl,
                            issues=issues,
                            llm_error=llm_error,
                            critic_notes=critic_notes,
                        ),
                        rec,
                        prev_state,
                    )
                )
                for _ in range(count)
            }
            try:
                result, dsl, res, attempt.autofixed = await self._race(mine, rivals)
            except (LLMRefusal, LLMIncomplete, LLMBadOutput) as e:
                llm_error = str(e)
                attempt.llm_error = llm_error
                attempt.seconds = round(time.perf_counter() - t0, 2)
                rec.attempts.append(attempt)
                gen.metrics.corrections += 1 if n > 1 else 0
                continue
            except EngineError as e:
                # The pool already restarted the worker and retried: this scene keeps breaking it.
                # Mark it degraded (spec Section 17); a working engine then compiles the fallback.
                attempt.llm_error = f"engine failed on this scene: {e}"
                attempt.seconds = round(time.perf_counter() - t0, 2)
                rec.attempts.append(attempt)
                llm_error = attempt.llm_error
                issues = []
                break
            attempt.model = result.model
            llm_error = None
            critic_notes = None
            errors = [i.model_dump() for i in res.issues if i.severity == "error"]
            warnings = [i.model_dump() for i in res.issues if i.severity == "warning"]
            attempt.ok = res.ok
            attempt.errors = errors[:40]
            attempt.warnings = len(warnings)
            if n > 1:
                gen.metrics.corrections += 1
            if res.ok and not critics_used and n < max_attempts and self._critics_on():
                critics_used = True
                report = await self._critique(gen, rec, dsl, res, previous_scenes, ctx)
                attempt.critic = report.model_dump() if report else None
                major = [i for i in (report.issues if report else []) if i.severity == "major"]
                if report is not None and report.verdict == "revise" and major:
                    critic_notes = "\n".join(f"- {i.problem} -> {i.fix}" for i in major)
                    attempt.seconds = round(time.perf_counter() - t0, 2)
                    rec.attempts.append(attempt)
                    issues = []
                    continue
            attempt.seconds = round(time.perf_counter() - t0, 2)
            rec.attempts.append(attempt)
            if res.ok and res.endState is not None:
                self._accept(rec, dsl, res, warnings, "ready")
                await changed()
                return
            issues = errors + [w for w in warnings if w.get("source") == "layout"][:5]

        reason = llm_error or (
            format_issues(issues, limit=3) if issues else "the scene could not be generated"
        )
        await self._degrade(rec, prev_state, reason)
        gen.metrics.degraded += 1
        await changed()

    def _shape(self, data: dict[str, Any], rec: SceneRecord) -> dict[str, Any]:
        return normalize(data, rec, independent=self.settings.animator_scene_mode == "parallel")

    async def _check(
        self, dsl: dict[str, Any], prev_state: SceneState | None
    ) -> tuple[dict[str, Any], CompileResult, bool]:
        """Compile, then apply the sure fixes for its errors (pipeline/tidy.py) and compile again.
        The fixed scene is kept when it has fewer errors, or as many but none of the old ones: the
        engine checks layout only once the timing and references are right, so new layout errors
        are progress. Three passes at most (a fix can uncover the next one)."""
        res = await self.engine.compile(dsl, prev_state)
        autofixed = False
        for _ in range(3):
            if res.ok:
                break
            fixed = autofix(dsl, [i.model_dump() for i in res.issues if i.severity == "error"])
            if fixed is None:
                break
            res_fixed = await self.engine.compile(fixed, prev_state)
            before, after = _keys(res), _keys(res_fixed)
            if not res_fixed.ok and (
                len(after) > len(before) or (len(after) == len(before) and after & before)
            ):
                break
            dsl, res, autofixed = fixed, res_fixed, True
        return dsl, res, autofixed

    async def _draft(
        self, call: Awaitable[LLMResult], rec: SceneRecord, prev_state: SceneState | None
    ) -> Draft:
        result = await call
        return (result, *await self._check(self._shape(result.data, rec), prev_state))

    async def _race(self, mine: set[asyncio.Task[Draft]], rivals: set[asyncio.Task[Draft]]) -> Draft:
        """This attempt's calls (`mine`: several drafts for a first attempt, `animator_scene_drafts`)
        race the drafts of earlier attempts still running (`rivals`). The first valid scene wins.
        When one of mine finishes invalid it is returned at once to be corrected, while the others
        keep racing as rivals of the correction: an unlucky draft rarely costs a round (ADR-033).
        Calls still running at the end finish in the background and their cost is counted (`settle`)."""
        pending: set[asyncio.Task[Draft]] = mine | rivals
        best: Draft | None = None
        unusable: BaseException | None = None
        try:
            while True:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                fatal = None
                for task in done:
                    exc = task.exception()
                    if isinstance(exc, (LLMRefusal, LLMIncomplete, LLMBadOutput)):
                        unusable = unusable or (exc if task in mine else None)
                    elif exc is not None:
                        fatal = fatal or exc
                    elif task.result()[2].ok:
                        return task.result()
                    elif task in mine and (best is None or _errors(task.result()) < _errors(best)):
                        best = task.result()
                if fatal is not None:
                    raise fatal  # auth, budget, outage, engine: the generation handles these
                if best is not None:
                    return best
                if not pending & mine:
                    assert unusable is not None
                    raise unusable
        finally:
            rivals.clear()
            rivals.update(pending)

    def _park(self, gen_id: str, tasks: set[asyncio.Task[Draft]]) -> None:
        for task in tasks:
            task.add_done_callback(_consume)  # a losing draft's failure matters to nobody
        self._background.setdefault(gen_id, set()).update(tasks)

    async def settle(self, gen_id: str) -> None:
        """Wait for a generation's drafts still running in the background (their cost is counted)."""
        tasks = self._background.pop(gen_id, set())
        if tasks:
            await asyncio.wait(tasks)

    def abandon(self, gen_id: str) -> None:
        for task in self._background.pop(gen_id, set()):
            task.cancel()

    def _critics_on(self) -> bool:
        return self.settings.animator_enable_llm_critic or (
            self.settings.animator_enable_visual_critic and self.keyframes is not None
        )

    async def _critique(
        self,
        gen: Generation,
        rec: SceneRecord,
        dsl: dict[str, Any],
        res: CompileResult,
        previous_scenes: list[dict[str, Any]],
        ctx: CallContext,
    ) -> Any:
        stages = await self.stages()
        images: list[bytes] = []
        if self.settings.animator_enable_visual_critic and self.keyframes is not None and res.beats:
            k = self.settings.animator_visual_critic_keyframes
            ends = [b.end - 0.1 for b in res.beats]
            step = max(1, len(ends) // k)
            times = [max(0.1, t) for t in ends[::step][:k]]
            images = await self.keyframes.keyframes([*previous_scenes, dsl], len(previous_scenes), times)
        try:
            return await stages.critic(gen, rec, summarize_for_critic(dsl, res), images, ctx)
        except (LLMRefusal, LLMIncomplete, LLMBadOutput):
            return None  # a failed review never blocks a valid scene

    def _accept(
        self,
        rec: SceneRecord,
        dsl: dict[str, Any],
        res: CompileResult,
        warnings: list[dict[str, Any]],
        status: Any,
    ) -> None:
        assert res.endState is not None
        rec.dsl = dsl
        rec.end_state = res.endState.model_dump()
        rec.state_summary = res.stateSummary
        rec.duration = res.duration
        rec.warnings = warnings[:40]
        rec.status = status
        rec.finished_at = time.time()

    async def _degrade(self, rec: SceneRecord, prev_state: SceneState | None, reason: str) -> None:
        fb = fallback_scene(rec)
        res = await self.engine.compile(fb, prev_state)
        rec.degraded_reason = reason[:600]
        if res.ok and res.endState is not None:
            self._accept(rec, fb, res, [], "degraded")
        else:
            rec.status = "failed"
            rec.finished_at = time.time()
