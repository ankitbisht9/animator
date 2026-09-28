"""The LLM stages: educational planner, storyboard (per chapter), animation planner, correction,
critic. Each builds one request (static instructions first, per-call context last), calls the
LLMClient and validates the result. Scene DSL is only shaped here; the engine worker validates it.
"""

from __future__ import annotations

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
from animator.pipeline.models import Chapter, EducationalPlan, Generation, SceneRecord, StoryboardChapter
from animator.pipeline.prompts import PromptPack, compact
from animator.pipeline.schemas import strict_schema

RETRYABLE = (LLMBadOutput, LLMRefusal, LLMIncomplete)


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
                    f"Language: {r.language}",
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
        msg = Message.user(
            "\n\n".join(
                [
                    f"Lesson plan:\n{compact(plan.model_dump())}",
                    f"Storyboard this chapter now: {chapter.id} ({chapter.title}), "
                    f"about {round(chapter.target_duration_s)} seconds.",
                    f"Its scenes will be numbered from {len(gen.scenes) + 1}.",
                    "Scenes already planned in earlier chapters:\n"
                    + (compact(earlier) if earlier else "none: this chapter opens the lesson"),
                    f"Objects on stage after those scenes: {', '.join(stage_objects(gen)) or 'none'}",
                    f"Audience: {gen.request.audience}. Language: {gen.request.language}.",
                ]
            )
        )
        req = self._request(
            "storyboard",
            self.pack.storyboard.version,
            self.pack.storyboard_instructions(),
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

    # ------------------------------------------------------------------ stage 3 + corrections

    def scene_context(self, gen: Generation, rec: SceneRecord, prev_summary: str | None) -> str:
        assert gen.plan is not None
        n = rec.index + 1
        total = max(len(gen.scenes), n)
        used = sorted(
            {oid.split(":")[0].strip() for s in gen.scenes[: rec.index] for oid in s.brief.objects_new}
        )
        return "\n\n".join(
            [
                f'Scene {n} of about {total}: id "{rec.id}", title "{rec.title}". '
                f'Title object id: "title_s{n:02d}".',
                f"Lesson: {gen.plan.title}. Audience: {gen.request.audience}. "
                f"Language: {gen.request.language}.",
                f"Storyboard brief:\n{compact(rec.brief.model_dump())}",
                f"Target duration: about {round(rec.brief.duration_s)} seconds.",
                "Previous scene:\n" + (prev_summary or "none: this is the first scene, the stage is empty."),
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
        critic) or repair it (the previous reply was unusable)."""
        parts = [self.scene_context(gen, rec, prev_summary)]
        if attempt == 1:
            stage = "animation_planner"
            parts.append("Write this scene.")
        else:
            stage = "correction_final" if final else "correction"
            if previous is not None:
                parts.append(f"Your previous version of this scene:\n{compact(previous)}")
            if issues:
                parts.append(
                    "The engine rejected it with these issues. Fix every error, keep what works, "
                    "and return the complete scene:\n" + format_issues(issues)
                )
            if critic_notes:
                parts.append(
                    "A reviewer asked for these changes. Apply them and return the complete scene:\n"
                    + critic_notes
                )
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
            "Scene",
            self.dsl_schema,
            self.settings.animator_scene_max_output_tokens,
            gen,
            f"scene {rec.index + 1} attempt {attempt}",
        )
        return await self.llm.generate(req, ctx)

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
