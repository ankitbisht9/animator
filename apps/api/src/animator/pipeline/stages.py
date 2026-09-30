"""The LLM stages: educational planner, storyboard (per chapter), animation planner, correction,
critic. Each builds one request (static instructions first, per-call context last), calls the
LLMClient and validates the result. Scene DSL is only shaped here; the engine worker validates it.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from animator.config import Settings
from animator.llm import (
    CallContext,
    ImagePart,
    LLMBadOutput,
    LLMClient,
    LLMIncomplete,
    LLMRefusal,
    LLMRequest,
    LLMResult,
    Message,
)
from animator.pipeline.language import language_line
from animator.pipeline.models import (
    Chapter,
    EducationalPlan,
    Generation,
    LessonOutline,
    OutlinePlan,
    SceneRecord,
    StoryboardChapter,
    StoryboardScene,
)
from animator.pipeline.prompts import PromptPack, compact
from animator.pipeline.schemas import apply_patch, patch_schema, strict_schema
from animator.pipeline.streaming import JsonScanner

RETRYABLE = (LLMBadOutput, LLMRefusal, LLMIncomplete)
log = logging.getLogger("animator.stages")


class CriticIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Literal["major", "minor"]
    problem: str
    fix: str


class CriticReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["pass", "revise"]
    issues: list[CriticIssue]


PLAN_SCHEMA = strict_schema(EducationalPlan)
STORYBOARD_SCHEMA = strict_schema(StoryboardChapter)
OUTLINE_SCHEMA = strict_schema(LessonOutline)
CRITIC_SCHEMA = strict_schema(CriticReport)


def _validated[M: BaseModel](model: type[M], result: LLMResult) -> M:
    try:
        return model.model_validate(result.data)
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise LLMBadOutput(f"{model.__name__} failed validation at {where}: {first['msg']}") from e


def format_issues(issues: list[dict[str, Any]], limit: int = 25) -> str:
    lines = []
    for i in issues[:limit]:
        tip = f" -> {i['suggestion']}" if i.get("suggestion") else ""
        where = i.get("path") or "/"
        lines.append(f"- [{i.get('severity', 'error')}] {i.get('code')} at {where}: {i.get('message')}{tip}")
    if len(issues) > limit:
        lines.append(f"- ... and {len(issues) - limit} more")
    return "\n".join(lines)


class Stages:
    def __init__(
        self, llm: LLMClient, settings: Settings, pack: PromptPack, dsl_schema: dict[str, Any]
    ) -> None:
        self.llm = llm
        self.settings = settings
        self.pack = pack
        self.dsl_schema = dsl_schema
        self.patch_schema = patch_schema(dsl_schema)

    def _request(
        self,
        stage: str,
        prompt_version: str,
        instructions: str,
        message: Message,
        schema_name: str,
        schema: dict[str, Any],
        max_out: int,
        gen: Generation,
        label: str,
    ) -> LLMRequest:
        models, effort = self.settings.stage_model(stage)
        return LLMRequest(
            stage=stage,
            prompt_version=prompt_version,
            instructions=instructions,
            messages=[message],
            schema_name=schema_name,
            schema=schema,
            models=models,
            effort=effort,
            max_output_tokens=max_out,
            generation_id=gen.id,
            label=label,
            # Keyed by the prompt pack, not the stage: drafts and corrections share instructions,
            # so they share the provider-side cache too.
            cache_key=f"animator-{prompt_version}",
        )

    async def _retrying[M: BaseModel](
        self, request: LLMRequest, model: type[M], ctx: CallContext, tries: int = 3
    ) -> M:
        """Plan and storyboard calls: a refused, truncated or invalid answer is retried (the error
        is shown to the model), up to `tries` times; then the last error is raised."""
        last: Exception | None = None
        base = request.messages[0]
        for n in range(tries):
            if last is not None:
                request.messages = [
                    base,
                    Message.user(
                        f"Your previous answer could not be used: {last}. "
                        "Answer again, following the schema exactly."
                    ),
                ]
                request.label = f"{request.label} (retry {n})"
            try:
                return _validated(model, await self.llm.generate(request, ctx))
            except RETRYABLE as e:
                last = e
        assert last is not None
        raise last

    # ------------------------------------------------------------------ stage 1

    async def plan(self, gen: Generation, ctx: CallContext) -> EducationalPlan:
        r = gen.request
        msg = Message.user(
            "\n".join(
                [
                    f"Topic: {r.topic}",
                    f"Audience: {r.audience}",
                    f"Duration: {round(r.duration_min * 60)} seconds",
                    f"Language: {language_line(r.language)}",
                    f"Style notes: {r.style}" if r.style else "Style notes: none",
                ]
            )
        )
        req = self._request(
            "educational_planner",
            self.pack.planner.version,
            self.pack.planner_instructions(),
            msg,
            "EducationalPlan",
            PLAN_SCHEMA,
            16_000,
            gen,
            "plan",
        )
        return one_chapter_if_short(await self._retrying(req, EducationalPlan, ctx), r.duration_min * 60)

    # ------------------------------------------------------------------ stage 2

    async def storyboard(self, gen: Generation, chapter_id: str, ctx: CallContext) -> StoryboardChapter:
        assert gen.plan is not None
        plan = gen.plan
        chapter = next(c for c in plan.chapters if c.id == chapter_id)
        earlier = [
            {
                "scene": s.index + 1,
                "title": s.title,
                "chapter": s.chapter_id,
                "objects_new": s.brief.objects_new,
                "objects_removed": s.brief.objects_removed,
                "transition": s.brief.transition,
            }
            for s in gen.scenes
        ]
        position = next(i for i, c in enumerate(plan.chapters) if c.id == chapter_id)
        if self.settings.animator_scene_mode == "parallel":
            # All chapters are storyboarded at the same time: no earlier scenes to refer to.
            context = [
                f"This is chapter {position + 1} of {len(plan.chapters)}; the other chapters are "
                "storyboarded at the same time"
                + (", so open with a quick reminder of where the lesson is." if position else ".")
            ]
        else:
            context = [
                f"Its scenes will be numbered from {len(gen.scenes) + 1}.",
                "Scenes already planned in earlier chapters:\n"
                + (compact(earlier) if earlier else "none: this chapter opens the lesson"),
                f"Objects on stage after those scenes: {', '.join(stage_objects(gen)) or 'none'}",
            ]
        msg = Message.user(
            "\n\n".join(
                [
                    f"Lesson plan:\n{compact(plan.model_dump())}",
                    f"Storyboard this chapter now: {chapter.id} ({chapter.title}), "
                    f"about {round(chapter.target_duration_s)} seconds.",
                    *context,
                    f"Audience: {gen.request.audience}. Language: {language_line(gen.request.language)}.",
                ]
            )
        )
        req = self._request(
            "storyboard",
            self.pack.storyboard.version,
            self.pack.storyboard_instructions(self.settings.animator_scene_mode),
            msg,
            "StoryboardChapter",
            STORYBOARD_SCHEMA,
            24_000,
            gen,
            f"storyboard {chapter.id}",
        )
        board = await self._retrying(req, StoryboardChapter, ctx)
        board.chapter_id = chapter.id
        return board

    # ------------------------------------------------------------------ stages 1 + 2 in one call

    async def outline(
        self,
        gen: Generation,
        ctx: CallContext,
        on_plan: Callable[[EducationalPlan], None] | None = None,
        on_scene: Callable[[StoryboardScene], None] | None = None,
    ) -> tuple[EducationalPlan, StoryboardChapter]:
        """Plan and storyboard a short lesson in one call (one round-trip instead of two).

        With `on_scene`, the reply is streamed: the plan and each scene brief are handed over as
        soon as they are complete, so scenes start while later briefs are still being written. Briefs
        handed over stay (a retry only adds the ones still missing)."""
        r = gen.request
        duration = round(r.duration_min * 60)

        def shape(plan: OutlinePlan, summary: list[str] | None = None) -> EducationalPlan:
            return plan.to_plan(r.audience, float(duration), summary or [])

        sink = None
        if on_scene is not None:
            sink = OutlineSink(shape, on_plan or (lambda plan: None), on_scene)
            ctx = ctx.streaming(sink)
        msg = Message.user(
            "\n".join(
                [
                    f"Topic: {r.topic}",
                    f"Audience: {r.audience}",
                    f"Duration: {duration} seconds",
                    f"Language: {language_line(r.language)}",
                    f"Style notes: {r.style}" if r.style else "Style notes: none",
                ]
            )
        )
        req = self._request(
            "storyboard",
            self.pack.outline.version,
            self.pack.outline_instructions(self.settings.animator_scene_mode),
            msg,
            "LessonOutline",
            OUTLINE_SCHEMA,
            16_000,
            gen,
            "outline",
        )
        try:
            out = await self._retrying(req, LessonOutline, ctx)
        except RETRYABLE as e:
            if sink is None or sink.plan is None or not sink.scenes:
                raise
            # Scenes are already being drawn: the lesson goes on with the briefs that arrived.
            log.warning(
                "outline for %s failed after %d scenes (%s): keeping them", gen.id, len(sink.scenes), e
            )
            kept = StoryboardChapter(chapter_id=sink.plan.chapters[0].id, scenes=list(sink.scenes))
            return sink.plan, kept
        if sink is not None and sink.plan is not None and sink.scenes:
            plan = sink.plan.model_copy(update={"summary": out.summary})
            scenes = list(sink.scenes) + out.scenes[len(sink.scenes) :]
        else:
            plan, scenes = shape(out.plan, out.summary), out.scenes
        return plan, StoryboardChapter(chapter_id=plan.chapters[0].id, scenes=scenes)

    # ------------------------------------------------------------------ stage 3 + corrections

    def _stage_start(self, prev_summary: str | None) -> str:
        if self.settings.animator_scene_mode == "parallel":
            return (
                "The stage starts empty: this scene is drawn on its own, at the same time as the other "
                "scenes. Create everything it shows (do not refer to objects of other scenes)."
            )
        return "Previous scene:\n" + (prev_summary or "none: this is the first scene, the stage is empty.")

    def scene_context(self, gen: Generation, rec: SceneRecord, prev_summary: str | None) -> str:
        assert gen.plan is not None
        n = rec.index + 1
        # While the outline is still streaming, later scenes are not known yet.
        total = f" of about {max(len(gen.scenes), n)}" if rec.chapter_id in gen.chapters_done else ""
        used = sorted(
            {oid.split(":")[0].strip() for s in gen.scenes[: rec.index] for oid in s.brief.objects_new}
        )
        return "\n\n".join(
            [
                f'Scene {n}{total}: id "{rec.id}", title "{rec.title}". Title object id: "title_s{n:02d}".',
                f"Lesson: {gen.plan.title}. Audience: {gen.request.audience}. "
                f"Language: {language_line(gen.request.language)}.",
                f"Storyboard brief:\n{compact(rec.brief.model_dump())}",
                f"Target duration: about {round(rec.brief.duration_s)} seconds.",
                self._stage_start(prev_summary),
                "Object ids used earlier in the lesson (keep their meaning): " + (", ".join(used) or "none"),
            ]
        )

    async def scene(
        self,
        gen: Generation,
        rec: SceneRecord,
        prev_summary: str | None,
        ctx: CallContext,
        *,
        attempt: int,
        final: bool,
        previous: dict[str, Any] | None = None,
        issues: list[dict[str, Any]] | None = None,
        llm_error: str | None = None,
        critic_notes: str | None = None,
    ) -> LLMResult:
        """Attempt 1 drafts the scene; later attempts correct it (issues from the engine or the
        critic) or repair it (the previous reply was unusable). A correction replies with only
        what changes (a patch, merged here), unless `animator_patch_corrections` is off."""
        parts = [self.scene_context(gen, rec, prev_summary)]
        patch = (
            attempt > 1
            and previous is not None
            and not llm_error
            and self.settings.animator_patch_corrections
        )
        reply = (
            "reply with only what changes: the objects and actions to add or replace (each complete, "
            "matched by id), the ids to delete, and duration, narration, camera, solvers or values only "
            "if they change (null otherwise). Everything else stays as it is, so only refer to objects "
            "and actions that exist or that you add, and when you delete one, also fix or delete "
            "whatever refers to it"
            if patch
            else "return the complete scene"
        )
        if attempt == 1:
            stage = "animation_planner"
            parts.append("Write this scene.")
        else:
            stage = "correction_final" if final else "correction"
            if previous is not None:
                parts.append(f"Your previous version of this scene:\n{compact(previous)}")
            if issues:
                parts.append(
                    f"The engine rejected it with these issues. Fix every error, keep what works, and "
                    f"{reply}:\n" + format_issues(issues)
                )
            if critic_notes:
                parts.append(f"A reviewer asked for these changes. Apply them and {reply}:\n" + critic_notes)
            if llm_error:
                parts.append(
                    f"Your previous reply could not be used ({llm_error}). "
                    "Write the scene again, more compactly if needed (fewer objects and actions), "
                    "following the schema exactly."
                )
        req = self._request(
            stage,
            self.pack.animation.version,
            self.pack.animation_instructions(),
            Message.user(*parts),
            "ScenePatch" if patch else "Scene",
            self.patch_schema if patch else self.dsl_schema,
            self.settings.animator_scene_max_output_tokens,
            gen,
            f"scene {rec.index + 1} attempt {attempt}",
        )
        result = await self.llm.generate(req, ctx)
        if patch:
            assert previous is not None
            return dataclasses.replace(result, data=apply_patch(previous, result.data))
        return result

    # ------------------------------------------------------------------ critics

    async def critic(
        self, gen: Generation, rec: SceneRecord, summary: str, images: list[bytes], ctx: CallContext
    ) -> CriticReport:
        stage = "visual_critic" if images else "critic"
        parts: list[Any] = [
            f"Scene brief:\n{compact(rec.brief.model_dump())}",
            f"What the engine will show:\n{summary}",
        ]
        if images:
            parts.append(f"{len(images)} keyframes of the rendered scene follow, in time order.")
            parts.extend(ImagePart(png, "high") for png in images)
        req = self._request(
            stage,
            self.pack.critic.version,
            self.pack.critic_instructions(),
            Message.user(*parts),
            "CriticReport",
            CRITIC_SCHEMA,
            8_000,
            gen,
            f"critic scene {rec.index + 1}",
        )
        return await self._retrying(req, CriticReport, ctx, tries=2)


class OutlineSink:
    """Receives a streaming outline reply: the plan, then each scene brief, as soon as each is
    complete and valid. A brief that is not valid on its own waits for the complete reply (it and
    the briefs after it are then taken from there)."""

    def __init__(
        self,
        shape: Callable[[OutlinePlan], EducationalPlan],
        on_plan: Callable[[EducationalPlan], None],
        on_scene: Callable[[StoryboardScene], None],
    ) -> None:
        self.shape = shape
        self.on_plan = on_plan
        self.on_scene = on_scene
        self.plan: EducationalPlan | None = None
        self.scenes: list[StoryboardScene] = []
        self.scanner = JsonScanner(self._value, self._item)

    def reset(self) -> None:
        self.scanner.reset()

    def feed(self, text: str) -> None:
        self.scanner.feed(text)

    def _value(self, key: str, value: Any) -> None:
        if key != "plan" or self.plan is not None:
            return
        try:
            self.plan = self.shape(OutlinePlan.model_validate(value))
        except ValidationError:
            return
        self.on_plan(self.plan)

    def _item(self, key: str, index: int, value: Any) -> None:
        if key != "scenes" or self.plan is None or index != len(self.scenes):
            return
        try:
            brief = StoryboardScene.model_validate(value)
        except ValidationError:
            return
        self.scenes.append(brief)
        self.on_scene(brief)


def one_chapter_if_short(plan: EducationalPlan, duration_s: float) -> EducationalPlan:
    """Lessons of 90 s or less are a single chapter (one storyboard call, scenes with room to breathe)."""
    if duration_s > 90 or len(plan.chapters) <= 1:
        return plan
    first = plan.chapters[0]
    merged = Chapter(
        id=first.id,
        title=plan.title,
        goal=" ".join(c.goal for c in plan.chapters),
        key_ideas=[i for c in plan.chapters for i in c.key_ideas],
        target_duration_s=sum(c.target_duration_s for c in plan.chapters),
        visual_approach=first.visual_approach,
    )
    return plan.model_copy(update={"chapters": [merged]})


def stage_objects(gen: Generation) -> list[str]:
    """Object ids expected on stage after the scenes planned so far (from the storyboard)."""
    alive: list[str] = []
    for s in gen.scenes:
        if s.brief.transition in ("fade", "cut"):
            alive = []
        for o in s.brief.objects_new:
            oid = o.split(":")[0].strip()
            if oid and oid not in alive:
                alive.append(oid)
        alive = [o for o in alive if o not in s.brief.objects_removed]
    return alive
