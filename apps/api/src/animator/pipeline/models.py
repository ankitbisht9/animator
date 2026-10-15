"""Pipeline artifacts (Pydantic v2): what the LLM stages produce and what a generation stores.

Stage 1 -> EducationalPlan, Stage 2 -> StoryboardChapter (per chapter, just ahead of Stage 3),
Stage 3 -> scene DSL (validated by the engine worker, never by Python).
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LAYOUTS = ("stage_with_caption", "stage_with_equation", "split", "title_card", "no_title")
Layout = Literal["stage_with_caption", "stage_with_equation", "split", "title_card", "no_title"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------- Stage 1


class KeyTerm(_Strict):
    term: str
    meaning: str = Field(description="One plain-language sentence a 12-year-old understands")


class Chapter(_Strict):
    id: str = Field(description="Short snake_case id, e.g. 'c1_hook'")
    title: str
    goal: str = Field(description="What the learner can do or explain after this chapter")
    key_ideas: list[str]
    target_duration_s: float
    visual_approach: str = Field(description="The central picture or metaphor that carries this chapter")


class EducationalPlan(_Strict):
    title: str
    subtitle: str
    subject: str = Field(
        description="physics, chemistry, biology, mathematics, computer_science, economics, ..."
    )
    audience: str
    target_duration_s: float
    hook: str = Field(description="The opening question or surprise that makes the learner curious")
    learning_objectives: list[str]
    prerequisites: list[str]
    key_terms: list[KeyTerm]
    misconceptions: list[str]
    chapters: list[Chapter]
    summary: list[str]

    @field_validator("chapters")
    @classmethod
    def _has_chapters(cls, v: list[Chapter]) -> list[Chapter]:
        if not v:
            raise ValueError("the plan needs at least one chapter")
        ids = [c.id for c in v]
        if len(set(ids)) != len(ids):
            raise ValueError("chapter ids must be unique")
        return v


# ---------------------------------------------------------------------- Stage 2


class BeatPlan(_Strict):
    narration: str = Field(description="One or two short spoken sentences; **word** marks emphasis")
    on_screen: str = Field(description="What visibly happens while this is said")


class Checkpoint(_Strict):
    question: str
    choices: list[str]
    answer: int = Field(description="Index of the correct choice (0-based)")
    explain: str


class StoryboardScene(_Strict):
    title: str
    purpose: str
    duration_s: float
    layout: Layout
    transition: Literal["continue", "fade", "cut"] = Field(
        description="continue keeps the stage (objects persist); fade/cut start from a clean stage"
    )
    visual_plan: str = Field(description="Concrete description of the picture and motion; no coordinates")
    objects_new: list[str] = Field(description="'id: short description' for each object introduced here")
    objects_kept: list[str] = Field(description="Ids of objects from earlier scenes that stay on stage")
    objects_removed: list[str] = Field(description="Ids of earlier objects removed during this scene")
    physics: str | None = Field(description="Solver to use and how, or null when no physics runs")
    beats: list[BeatPlan]
    engagement: str = Field(description="The attention device of this scene")
    checkpoint: Checkpoint | None


def _check_scenes(v: list[StoryboardScene]) -> list[StoryboardScene]:
    if not v:
        raise ValueError("at least one scene is needed")
    for s in v:
        if not s.beats:
            raise ValueError(f"scene '{s.title}' has no narration beats")
        if s.checkpoint is not None and not 0 <= s.checkpoint.answer < len(s.checkpoint.choices):
            raise ValueError(f"scene '{s.title}': checkpoint answer is not one of its choices")
    return v


class StoryboardChapter(_Strict):
    chapter_id: str
    scenes: list[StoryboardScene]

    @field_validator("scenes")
    @classmethod
    def _has_scenes(cls, v: list[StoryboardScene]) -> list[StoryboardScene]:
        return _check_scenes(v)


class OutlinePlan(_Strict):
    """The few plan fields a short lesson needs (written first, so the scenes can start sooner)."""

    title: str
    subtitle: str
    subject: str = Field(
        description="physics, chemistry, biology, mathematics, computer_science, economics, ..."
    )
    hook: str = Field(description="The opening question or surprise that makes the learner curious")
    learning_objectives: list[str]
    visual_approach: str = Field(description="The central picture or metaphor that carries the lesson")

    def to_plan(self, audience: str, duration_s: float, summary: list[str]) -> EducationalPlan:
        """The full plan of a one-chapter lesson."""
        chapter = Chapter(
            id="c1",
            title=self.title,
            goal=self.learning_objectives[0] if self.learning_objectives else self.hook,
            key_ideas=self.learning_objectives,
            target_duration_s=duration_s,
            visual_approach=self.visual_approach,
        )
        return EducationalPlan(
            title=self.title,
            subtitle=self.subtitle,
            subject=self.subject,
            audience=audience,
            target_duration_s=duration_s,
            hook=self.hook,
            learning_objectives=self.learning_objectives,
            prerequisites=[],
            key_terms=[],
            misconceptions=[],
            chapters=[chapter],
            summary=summary,
        )


class LessonOutline(_Strict):
    """Plan and storyboard in one answer, for short lessons (one round-trip instead of two)."""

    plan: OutlinePlan
    scenes: list[StoryboardScene]
    summary: list[str]

    @field_validator("scenes")
    @classmethod
    def _has_scenes(cls, v: list[StoryboardScene]) -> list[StoryboardScene]:
        return _check_scenes(v)


# ---------------------------------------------------------------------- the teaching pipeline (ADR-044)

TeachingStep = Literal[
    "hook", "prior_knowledge", "intuition", "concept", "example", "misconception", "apply", "recap"
]


class ScriptBeat(_Strict):
    say: str = Field(
        description="What the narrator says: one or two spoken sentences; **word** marks the key word"
    )
    show: str = Field(description="What the learner sees while this is said (concrete, drawable)")


class ScriptScene(_Strict):
    title: str = Field(description="Short scene title, in English (it is shown on screen)")
    step: TeachingStep
    goal: str = Field(description="What the learner understands after this scene")
    beats: list[ScriptBeat]


class TeachingScript(_Strict):
    """The lesson as a master teacher would explain it: every spoken word, and what is seen."""

    title: str
    subtitle: str
    subject: str = Field(
        description="physics, chemistry, biology, mathematics, computer_science, economics, ..."
    )
    learner: str = Field(description="Who the learner is and what they already know")
    hook: str = Field(description="The opening question or surprise")
    learning_objectives: list[str]
    core_idea: str = Field(description="The one sentence the learner must remember")
    intuition: str = Field(description="The everyday picture or analogy that makes it click")
    misconception: str = Field(description="The most likely wrong belief, and why it is wrong")
    worked_example: str | None = Field(
        description="A concrete example with real, simple numbers, step by step; null if none fits"
    )
    key_terms: list[KeyTerm]
    scenes: list[ScriptScene]
    summary: list[str]

    @field_validator("scenes")
    @classmethod
    def _has_scenes(cls, v: list[ScriptScene]) -> list[ScriptScene]:
        if not v:
            raise ValueError("the script needs at least one scene")
        for s in v:
            if not s.beats or not all(b.say.strip() for b in s.beats):
                raise ValueError(f"scene '{s.title}' needs beats with narration")
        return v


class ScriptReview(_Strict):
    changes: list[str] = Field(description="What was fixed or improved, one line each; empty if nothing")
    script: TeachingScript


class DirectedScene(_Strict):
    scene: int = Field(description="The script scene this plans (1-based)")
    layout: Layout
    visual_plan: str = Field(
        description="Composition: the central picture, its region and size, where everything else sits, "
        "what moves; no coordinates"
    )
    objects_new: list[str] = Field(description="'id: short description' for each object in the scene")
    physics: str | None = Field(description="Solver to use and how, or null when no physics runs")
    beats: list[str] = Field(
        description="One per script beat, in order: exactly what appears, moves, changes or is "
        "highlighted while that beat is said"
    )
    engagement: str = Field(description="The attention device of this scene")
    checkpoint: Checkpoint | None


class Direction(_Strict):
    look: str = Field(
        description="The lesson's visual identity: recurring objects (id, shape, colour role, size, "
        "place) and colour meanings, kept the same in every scene"
    )
    scenes: list[DirectedScene]


# ---------------------------------------------------------------------- generation records

GenerationStatus = Literal["queued", "planning", "generating", "complete", "failed", "cancelled", "paused"]
SceneStatus = Literal["planned", "generating", "validating", "correcting", "ready", "degraded", "failed"]
TERMINAL: tuple[GenerationStatus, ...] = ("complete", "failed", "cancelled")


class GenerationRequest(_Strict):
    topic: str = Field(min_length=3, max_length=500)
    audience: str = Field(default="high-school student", max_length=120)
    duration_min: float = Field(default=3.0, ge=0.5, le=20.0)
    style: str | None = Field(default=None, max_length=300)
    language: str = Field(default="English", max_length=40)
    paced: bool = Field(default=False, description="Generate only a buffer ahead of playback (saves cost)")
    # How the scenes are laid out (ADR-047): classic or planned, or costsaver (ADR-055: planned, with
    # saved drawings placed where they fit exactly); None: the server's default (ANIMATOR_LAYOUT_MODE).
    layout: Literal["classic", "planned", "costsaver"] | None = None
    # Model trials (ADR-056): an OpenRouter model (one of ANIMATOR_TRIAL_MODELS) this lesson is made
    # with instead of the configured ones, to compare it; None: the normal setup.
    trial_model: str | None = Field(default=None, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/~-]*$")


class Attempt(BaseModel):
    n: int
    kind: Literal["plan", "correction", "repair", "critic"]
    stage: str
    model: str | None = None
    ok: bool = False
    errors: list[dict[str, Any]] = Field(default_factory=list)
    warnings: int = 0
    llm_error: str | None = None
    critic: dict[str, Any] | None = None
    # The engine's errors had sure fixes, applied without another model call (pipeline/tidy.py).
    autofixed: bool = False
    seconds: float = 0.0


class SceneRecord(BaseModel):
    index: int
    id: str
    title: str
    chapter_id: str
    brief: StoryboardScene
    status: SceneStatus = "planned"
    dsl: dict[str, Any] | None = None
    end_state: dict[str, Any] | None = None
    state_summary: str | None = None
    duration: float | None = None
    attempts: list[Attempt] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    degraded_reason: str | None = None
    # Spoken narration (TTS on): per beat, its audio file and when each caption word is said.
    narration: dict[str, Any] | None = None
    # The planned layout flow (ADR-047): the scene's stage plan and what the planner made of it.
    layout_plan: dict[str, Any] | None = None
    # Cost-saver lessons (ADR-055): the saved drawings offered to the scene and the ones it placed.
    saved: dict[str, Any] | None = None
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def done(self) -> bool:
        return self.status in ("ready", "degraded", "failed")

    @property
    def playable(self) -> bool:
        return self.status in ("ready", "degraded") and self.dsl is not None


class Metrics(BaseModel):
    llm_calls: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    corrections: int = 0
    degraded: int = 0
    first_play_s: float | None = None
    generation_s: float | None = None
    slow_scenes: list[str] = Field(default_factory=list)
    storyboard_fallbacks: int = 0
    # Narration audio (TTS on): characters sent to the provider (free tiers count them), seconds
    # of speech, and scenes left without a voice because synthesis failed.
    tts_chars: int = 0
    tts_audio_s: float = 0.0
    tts_failures: int = 0
    tts_error: str | None = None  # why the last failure happened (shown on the lesson page)
    # Visual glitch repair (ADR-040): patch rounds run, and glitches the finished scenes still show.
    glitch_repairs: int = 0
    glitches_left: int = 0
    # Cost-saver lessons (ADR-055): saved drawings placed in the finished scenes.
    saved_objects: int = 0
    # The critic's second look at a revision it asked for (ADR-061), and revisions it turned down.
    second_looks: int = 0
    revisions_turned_down: int = 0


class Generation(BaseModel):
    id: str = Field(default_factory=lambda: "g_" + uuid.uuid4().hex[:12])
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    request: GenerationRequest
    status: GenerationStatus = "queued"
    error: str | None = None
    plan: EducationalPlan | None = None
    # The teaching pipeline's work (ADR-044): the reviewed script, what the review changed, and the
    # lesson's visual identity. None for lessons made by the fast pipeline.
    teaching: dict[str, Any] | None = None
    chapters_done: list[str] = Field(default_factory=list)
    scenes: list[SceneRecord] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)
    models: dict[str, list[str]] = Field(default_factory=dict)
    playhead: float = 0.0
    version: int = 0

    def summary(self) -> dict[str, Any]:
        """Everything but scene DSL and end states (progress UI, lists)."""
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "request": self.request.model_dump(),
            "status": self.status,
            "error": self.error,
            "title": self.plan.title if self.plan else None,
            "subtitle": self.plan.subtitle if self.plan else None,
            "planned_duration_s": self.plan.target_duration_s
            if self.plan
            else self.request.duration_min * 60,
            "chapters": [
                {"id": c.id, "title": c.title, "storyboarded": c.id in self.chapters_done}
                for c in (self.plan.chapters if self.plan else [])
            ],
            "scenes": [
                {
                    "index": s.index,
                    "id": s.id,
                    "title": s.title,
                    "chapter_id": s.chapter_id,
                    "status": s.status,
                    "duration": s.duration if s.duration is not None else s.brief.duration_s,
                    "attempts": len(s.attempts),
                    "degraded_reason": s.degraded_reason,
                }
                for s in self.scenes
            ],
            "metrics": self.metrics.model_dump(),
            "version": self.version,
        }
