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

import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from animator.config import Settings
from animator.engine_client import EngineClient
from animator.engine_client.errors import EngineError
from animator.llm import CallContext, LLMBadOutput, LLMClient, LLMIncomplete, LLMRefusal
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
from animator_contracts.protocol import CompileResult, SceneState

Changed = Callable[[], Awaitable[None]]


class KeyframeSource(Protocol):
    """Renders PNG keyframes of scene `index` of a lesson (for the visual critic)."""

    async def keyframes(
        self, scenes: list[dict[str, Any]], index: int, times: list[float]
    ) -> list[bytes]: ...


def scene_records(gen: Generation, board: StoryboardChapter) -> list[SceneRecord]:
    start = len(gen.scenes)
    out = []
    for i, brief in enumerate(board.scenes):
        index = start + i
        out.append(
            SceneRecord(
                index=index,
                id=f"s{index + 1:02d}_{slug(brief.title)}",
                title=brief.title[:120],
                chapter_id=board.chapter_id,
                brief=brief,
            )
        )
    return out


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


def normalize(dsl: dict[str, Any], rec: SceneRecord) -> dict[str, Any]:
    """Pin the fields the pipeline owns (id, version); the rest is the model's."""
    dsl = dict(dsl)
    dsl["dsl_version"] = "1.0"
    dsl["id"] = rec.id
    if not dsl.get("title"):
        dsl["title"] = rec.title
    if rec.brief.transition != "continue" and not dsl.get("transition_in"):
        dsl["transition_in"] = {"type": rec.brief.transition}
    return dsl


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
        stages = await self.stages()
        prev_state = SceneState.model_validate(prev.end_state) if prev and prev.end_state else None
        prev_summary = prev.state_summary if prev else None
        max_attempts = 1 + max(0, self.settings.animator_max_correction_attempts)
        rec.attempts = []
        rec.started_at = rec.started_at or time.time()
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
            try:
                result = await stages.scene(
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
                )
            except (LLMRefusal, LLMIncomplete, LLMBadOutput) as e:
                llm_error = str(e)
                attempt.llm_error = llm_error
                attempt.seconds = round(time.perf_counter() - t0, 2)
                rec.attempts.append(attempt)
                gen.metrics.corrections += 1 if n > 1 else 0
                continue
            attempt.model = result.model
            llm_error = None
            critic_notes = None
            dsl = normalize(result.data, rec)
            rec.status = "validating"
            await changed()
            try:
                res = await self.engine.compile(dsl, prev_state)
            except EngineError as e:
                # The pool already restarted the worker and retried: this scene keeps breaking it.
                # Mark it degraded (spec Section 17); a working engine then compiles the fallback.
                attempt.llm_error = f"engine failed on this scene: {e}"
                attempt.seconds = round(time.perf_counter() - t0, 2)
                rec.attempts.append(attempt)
                llm_error = attempt.llm_error
                issues = []
                break
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
