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
import copy
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from animator.config import Settings
from animator.engine_client import EngineClient
from animator.engine_client.errors import EngineError
from animator.llm import CallContext, LLMBadOutput, LLMClient, LLMIncomplete, LLMRefusal, LLMResult
from animator.objects.models import SavedObject
from animator.objects.prompting import offer_text
from animator.objects.resolve import Resolution, placed_refs
from animator.objects.resolve import compact as compact_saved
from animator.objects.resolve import resolve as resolve_saved
from animator.objects.service import ObjectLibrary
from animator.pipeline.layout_plan import LayoutPlanner, StagePlan, parse_plan, repair_draft
from animator.pipeline.layout_plan.figures import sample_times
from animator.pipeline.layout_plan.relations import (
    TOLERANCE,
    Verdict,
    describe,
    expand,
    implied,
    intended,
    resolve_kinds,
    snap,
    verify,
    worth_reporting,
)
from animator.pipeline.models import (
    Attempt,
    BeatPlan,
    Chapter,
    Generation,
    SceneRecord,
    StoryboardChapter,
    StoryboardScene,
    TeachingScript,
)
from animator.pipeline.narration import (
    Part,
    floors,
    narration_record,
    needs,
    retime,
    speak_beats,
    speak_scene,
    speak_texts,
    split_take,
    sync_highlights,
    take_needs,
    take_record,
)
from animator.pipeline.prompts import PromptPack, slug
from animator.pipeline.stages import CriticIssue, CriticReport, Stages, format_issues
from animator.pipeline.tidy import autofix, salvage, tidy
from animator.tts import SpeechCache, Spoken, caption_words
from animator.tts.script import Script, SpeechScripter, wants_script
from animator_contracts.protocol import CompileResult, SceneState, ValidationIssue

Changed = Callable[[], Awaitable[None]]

log = logging.getLogger("animator.pipeline")


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


def cues_of(rec: SceneRecord) -> dict[str, str]:
    """What the picture shows while each sentence of the scene is said (from its brief): the voice
    stresses what is revealed (ADR-045)."""
    return {b.narration: b.on_screen for b in rec.brief.beats if b.on_screen}


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

# Label relocation (ADR-046): labels tried per scene, places tried per label, and compiles per scene.
RELOCATE_LABELS = 4
RELOCATE_TRIES = 14
RELOCATE_COMPILES = 40


# First words of ids that name a kind of object, not a drawing ("label_sourcing", "arrow_1").
_NOT_A_DRAWING = {"label", "labels", "arrow", "link", "text", "note", "badge", "card", "title", "line"}


def _drawing_of(ref: str, boxes: dict[str, dict[str, float]]) -> tuple[float, float, float, float] | None:
    """The box of the whole drawing `ref` is a part of: the objects sharing the first word of its id
    (`grape_cluster_berry_4` with `grape_cluster_berry_1`... and `grape_cluster_leaf`). None when `ref`
    stands alone."""
    head = ref.split("_")[0]
    if not head or head in _NOT_A_DRAWING:
        return None
    parts = [b for oid, b in boxes.items() if oid.split("_")[0] == head]
    if len(parts) < 3:
        return None
    return (
        min(b["x"] for b in parts),
        min(b["y"] for b in parts),
        max(b["x"] + b["w"] for b in parts),
        max(b["y"] + b["h"] for b in parts),
    )


def _inside_a_frame(label: str, own: dict[str, float], boxes: dict[str, dict[str, float]]) -> bool:
    """A label lying (90% or more) within an object at least six times its size is meant to be inside
    it: the address on a browser bar, a heading in a card, a value in a box. It stays there."""
    area = max(1.0, own["w"] * own["h"])
    for oid, b in boxes.items():
        if oid == label or b["w"] * b["h"] < 6 * area:
            continue
        w = min(own["x"] + own["w"], b["x"] + b["w"]) - max(own["x"], b["x"])
        h = min(own["y"] + own["h"], b["y"] + b["h"]) - max(own["y"], b["y"])
        if w > 0 and h > 0 and w * h >= 0.9 * area:
            return True
    return False


# Relations (ADR-048): snapping passes per scene, and how far off (px) a relation still broken is
# reported to the reviewer.
RELATION_PASSES = 2
RELATION_REPORT_PX = 12.0
RELATION_SINGLES = 6  # when snapping them together does not help: this many tried one at a time

# The planned layout (ADR-047): a layout that would shrink anything below this is not used (items
# with text already stop at 0.85x, see layout_plan/figures.py).
PLANNED_LEAST_SCALE = 0.6
PLANNED_RESCUE_GLITCHES = 4

# Auto-framing (ADR-044): the stage on screen (between the title bar and the captions), how much of it
# the framed content fills, and the most the camera zooms in.
STAGE = (48.0, 116.0, 1552.0, 736.0)
FRAME_FILL = 0.86
FRAME_MAX_ZOOM = 1.5


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


def describe_diagram(params: dict[str, Any]) -> str:
    """A diagram in a line: its layout, nodes (label) and edges (ADR-061)."""
    nodes = [n for n in params.get("nodes") or [] if isinstance(n, dict)]
    edges = [e for e in params.get("edges") or [] if isinstance(e, dict)]
    shown = ", ".join(
        f"{n.get('id')}"
        + (f" '{n.get('label')}'" if n.get("label") else "")
        + (" (ghost)" if n.get("ghost") else "")
        for n in nodes[:24]
    )
    joins = ", ".join(f"{e.get('from')}->{e.get('to')}" for e in edges[:30])
    return f"{params.get('layout') or 'layered'} diagram; nodes: {shown}; edges: {joins or 'none'}"


def summarize_for_critic(dsl: dict[str, Any], res: CompileResult) -> str:
    objs = []
    for o in dsl.get("objects") or []:
        params = o.get("params") or {}
        text = next(
            (str(params[k]) for k in ("text", "label", "title", "tex") if isinstance(params.get(k), str)), ""
        )
        kind = o.get("type")
        if kind == "figure":  # a saved drawing (ADR-055), by what it shows
            kind, text = "saved drawing", str((o.get("metadata") or {}).get("description") or text)
        elif kind == "diagram":  # laid out by the engine (ADR-061): what it joins
            text = describe_diagram(params)
        limit = 400 if kind == "diagram" else 80
        objs.append(f"- {o.get('id')} ({kind}){': ' + text[:limit] if text else ''}")
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


# Layout problems the polish pass works on (warnings: the scene plays, but looks untidy).
UNTIDY = {
    "LABEL_OCCLUDES",
    "UNDER_CAPTION",
    "CONNECTOR_CROSSES_LABEL",
    "CONNECTOR_CROSSES_OBJECT",
    "CONNECTORS_CROSS",
    "OBJECT_OVERLAP",
    "NODE_OVERLAP",
    "PATH_OFF_ITS_POINTS",
    "UNSUPPORTED_GLYPH",
    "NEAR_EDGE",
    "HIDDEN_TEXT",
    "OUT_OF_STAGE",
    "TEXT_WRAPPED",
    "LEAVES_CONTAINER",
    "TEXT_WORD_BROKEN",
}

# What a viewer sees as a glitch: text hidden or cut, things out of the stage, one thing drawn into
# another (two nodes of a diagram piled on one spot, a branch line beside its commits: ADR-060), a row
# that wraps. Counted per lesson (`glitches_left`); a new timing for the voice must not add one.
# (ADR-040's model repair round for them was reverted in ADR-042.)
GLITCHES = {
    "HIDDEN_TEXT",
    "OUT_OF_STAGE",
    "NEAR_EDGE",
    "UNDER_CAPTION",
    "TEXT_WRAPPED",
    "LEAVES_CONTAINER",
    "OBJECT_OVERLAP",
    "NODE_OVERLAP",
    "PATH_OFF_ITS_POINTS",
    "LABEL_OCCLUDES",
    "CONNECTOR_CROSSES_LABEL",
    "TEXT_TOO_SMALL",
    "TEXT_WORD_BROKEN",
}

# A label or text that these name stands where it covers, hides under or is crossed by something: the
# label is moved (ADR-046). The message names the label first, or second for a connector across it.
LABEL_TROUBLE = {"LABEL_OCCLUDES", "HIDDEN_TEXT", "LABEL_OVERLAP", "CONNECTOR_CROSSES_LABEL"}
_QUOTED_IDS = re.compile(r"'([A-Za-z0-9_]+)'")


def troubled_labels(res: CompileResult) -> list[str]:
    """The labels a valid scene still shows in trouble, in the order the engine reports them."""
    out: list[str] = []
    for i in res.issues:
        if i.severity != "warning" or i.code not in LABEL_TROUBLE:
            continue
        ids = _QUOTED_IDS.findall(i.message)
        if len(ids) < 2:
            continue
        label = ids[1] if i.code == "CONNECTOR_CROSSES_LABEL" else ids[0]
        if label not in out:
            out.append(label)
    return out


def _trouble_with(res: CompileResult, label: str) -> int:
    return sum(
        1
        for i in res.issues
        if i.severity == "warning" and i.code in LABEL_TROUBLE and f"'{label}'" in i.message
    )


def planned_glitches(res: CompileResult, plan: StagePlan | None) -> int:
    """Visual glitches of a planned scene: contact its relations mean (a car on its road, juice in its
    glass, ADR-048) is not one."""
    rels = plan.relations if plan is not None else []
    return sum(1 for i in glitches_of(res) if not (rels and intended(i.message, rels)))


def layout_only(res: CompileResult) -> bool:
    """A draft that failed only its layout checks (things overlapping, off the stage): it compiled,
    and the planned flow's planner can still lay it out (ADR-047)."""
    errors = [i for i in res.issues if i.severity == "error"]
    return bool(errors) and all(i.source == "layout" for i in errors)


def glitches_of(res: CompileResult) -> list[Any]:
    """A label covering an object counts only when it straddles the object's edge (the engine then
    says where to move it); a letter written on a magnet's pole is meant to be there."""
    return [
        i
        for i in res.issues
        if i.severity == "warning"
        and i.code in GLITCHES
        and (i.code != "LABEL_OCCLUDES" or i.fix is not None)
    ]


def _untidy(res: CompileResult) -> int:
    return sum(1 for i in res.issues if i.severity == "warning" and i.code in UNTIDY)


def _errors(d: Draft) -> int:
    return _count(d[2])


def _with_problems(res: CompileResult, saved: Resolution) -> CompileResult:
    """A draft that names saved drawings it was not offered (ADR-055) is never accepted: the engine's
    errors about those objects give way to the explanation, and the draft goes to a correction."""
    others = [
        i for i in res.issues if not any(i.path == f or i.path.startswith(f + "/") for f in saved.flagged)
    ]
    ours = [ValidationIssue.model_validate(p) for p in saved.problems]
    return res.model_copy(update={"ok": False, "issues": ours + others})


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
        speech: SpeechCache | None = None,
        objects: ObjectLibrary | None = None,
    ) -> None:
        self.engine = engine
        self.llm = llm
        self.settings = settings
        self.keyframes = keyframes
        # Narration audio; None when ANIMATOR_TTS_ENABLED is off (then nothing here speaks).
        self.speech = speech
        # The speech script (what the voice reads: Hindi in Devanagari, spoken punctuation, tone).
        self.scripter = (
            SpeechScripter(
                llm,
                settings,
                settings.resolve(settings.animator_tts_cache_dir) / "scripts",
                directed=settings.animator_tts_prosody,
            )
            if speech is not None and (settings.animator_tts_script != "off" or settings.animator_tts_prosody)
            else None
        )
        self._stages: Stages | None = None
        # Losing scene drafts still running, per generation (see `_drafts`).
        self._background: dict[str, set[asyncio.Task[Any]]] = {}
        # The planned layout flow (ADR-047): the planner, and what it reported per scene reply.
        self.layout_planner = LayoutPlanner(engine)
        self._plan_reports: dict[int, dict[str, Any]] = {}
        # Cost-saver lessons (ADR-055): the saved drawings; None when ANIMATOR_OBJECTS_ENABLED is off.
        self.objects = objects or (ObjectLibrary(settings) if settings.animator_objects_enabled else None)

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
        if self.speech is not None:
            # The storyboard's sentences are what the scene will say: script and synthesize them while
            # the scene is being written, so the voice adds no waiting.
            task = asyncio.ensure_future(
                self._voice(gen, [b.narration for b in rec.brief.beats], ctx, cues_of(rec))
            )
            task.add_done_callback(_consume)
        try:
            await self._attempts(
                gen, rec, prev_summary, prev_state, ctx, changed, previous_scenes, max_attempts, rivals
            )
        finally:
            self._park(gen.id, rivals)
        if rec.saved is not None:
            await self._saved_used(gen, rec)
        if self.speech is not None and rec.playable:
            await self._narrate(gen, rec, prev_state, ctx)

    async def _scripts(
        self, gen: Generation, texts: list[str], ctx: CallContext | None, cues: dict[str, str] | None = None
    ) -> dict[str, Script]:
        """The speech script of a scene's sentences, when this lesson's language gets one (ADR-041),
        or for every lesson with voice modulation (ADR-045), which also directs the delivery and is
        told what the picture shows while each sentence is said (`cues`)."""
        if self.scripter is None:
            return {}
        if not self.scripter.directed and not wants_script(
            self.settings.animator_tts_script, gen.request.language
        ):
            return {}
        return await self.scripter.scripts(texts, gen.request.language, ctx, gen.id, cues)

    async def _voice(
        self, gen: Generation, texts: list[str], ctx: CallContext | None, cues: dict[str, str] | None = None
    ) -> Any:
        """A scene's narration in this lesson's voice: in one take (ADR-043), or sentence by sentence,
        each knowing its neighbours (ADR-041)."""
        assert self.speech is not None
        language = gen.request.language
        scripts = await self._scripts(gen, texts, ctx, cues)
        voice, provider = self.speech.voice_for(language), self.speech.provider_for(language)
        if self.settings.animator_tts_one_take:
            return await speak_scene(self.speech, texts, voice, provider, scripts)
        return await speak_texts(self.speech, texts, voice, provider, scripts)

    async def _narrate(
        self, gen: Generation, rec: SceneRecord, prev_state: SceneState | None, ctx: CallContext | None = None
    ) -> None:
        """Give a finished scene its voice (pipeline/narration.py). Any failure leaves the scene as it
        was, with captions only: speech never fails or delays a lesson beyond its own synthesis."""
        assert self.speech is not None and rec.dsl is not None
        try:
            res = await self.engine.compile(rec.dsl, prev_state)
            beats = list(res.beats or [])
            if not res.ok or not beats:
                return
            voice = self.speech.voice_for(gen.request.language)
            provider = self.speech.provider_for(gen.request.language)
            scripts = await self._scripts(gen, [b.text for b in beats], ctx, cues_of(rec))
            take: Spoken | None = None
            parts: dict[str, Part] = {}
            spoken: dict[str, Spoken] = {}
            if self.settings.animator_tts_one_take:
                # The whole scene in one recording (ADR-043); each beat lasts exactly its part of it.
                take = await speak_scene(self.speech, [b.text for b in beats], voice, provider, scripts)
                if take is None:
                    return
                parts = split_take(beats, take)
                need = take_needs(parts, beats, self.settings.animator_tts_scene_pause_s)
            else:
                spoken = await speak_beats(self.speech, beats, voice, provider, scripts)
                if not spoken:
                    return
                need = needs(spoken, beats, self.settings.animator_tts_pause_s)
            # Fit each sentence's time to its speech (ADR-041): first also shortening long silences,
            # then only halfway, then, if the engine rejects both, only lengthening beats the speech
            # outgrows. A timing is kept only when the scene stays valid and shows no more visual
            # glitches than before.
            tries = [retime(rec.dsl, beats, need)]
            if self.settings.animator_tts_fit_beats:
                floor = floors(rec.dsl, beats)
                tries[:0] = [
                    retime(rec.dsl, beats, need, floor=floor),
                    retime(rec.dsl, beats, need, floor=floor, keep=0.5),
                ]
            before = len(glitches_of(res))
            for retimed in tries:
                if retimed is None:
                    continue
                fixed, again, _ = await self._check(retimed, prev_state)
                if (
                    again.ok
                    and again.endState is not None
                    and again.beats
                    and len(glitches_of(again)) <= before
                ):
                    warnings = [i.model_dump() for i in again.issues if i.severity == "warning"]
                    self._accept(rec, fixed, again, warnings, rec.status)
                    beats = list(again.beats)
                    break
            else:
                if any(t is not None for t in tries):
                    log.warning("scene %s: no retiming kept the scene valid; timing kept", rec.id)
            if take is not None:
                rec.narration = take_record(beats, parts, take, provider.name, voice)
                audio_s = take.duration_s
            else:
                rec.narration = narration_record(beats, spoken, provider.name, voice)
                audio_s = sum(s.duration_s for s in spoken.values())
            if self.settings.animator_tts_prosody and self.settings.animator_tts_sync_highlights and scripts:
                await self._sync_highlights(rec, prev_state, scripts, before)
            gen.metrics.tts_chars += sum(len(b.text) for b in beats if b.id in need)
            gen.metrics.tts_audio_s = round(gen.metrics.tts_audio_s + audio_s, 2)
        except Exception as e:
            gen.metrics.tts_failures += 1
            gen.metrics.tts_error = str(e)[:300]
            log.warning("scene %s: no narration audio (%s)", rec.id, e)

    async def _sync_highlights(
        self, rec: SceneRecord, prev_state: SceneState | None, scripts: dict[str, Script], before: int
    ) -> None:
        """Voice and picture together (ADR-045): a beat's highlights move to the moment the thing they
        highlight is named (stressed by the voice where it is). Kept only when the scene stays valid,
        shows no more visual glitches and its beats keep their times (the narration is timed to them)."""
        if rec.dsl is None or rec.narration is None:
            return
        texts = {
            str(b.get("id")): str(b.get("text", ""))
            for b in (rec.dsl.get("narration") or {}).get("beats") or []
        }
        words: dict[str, list[tuple[str, float]]] = {}
        stressed: dict[str, frozenset[int]] = {}
        for entry in rec.narration.get("beats") or []:
            text = texts.get(entry["id"], "")
            times = entry.get("words") or []
            words[entry["id"]] = [(w, float(t[0])) for w, t in zip(caption_words(text), times, strict=False)]
            script = scripts.get(text)
            if script is not None:
                stressed[entry["id"]] = script.stress
        synced = sync_highlights(rec.dsl, words, stressed)
        if synced is None:
            return
        fixed, again, _ = await self._check(synced, prev_state)
        starts = {e["id"]: float(e["start"]) for e in rec.narration.get("beats") or []}
        kept_timing = all(abs(b.start - starts[b.id]) < 0.01 for b in again.beats or [] if b.id in starts)
        if again.ok and again.endState is not None and kept_timing and len(glitches_of(again)) <= before:
            warnings = [i.model_dump() for i in again.issues if i.severity == "warning"]
            self._accept(rec, fixed, again, warnings, rec.status)

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
        critic_rounds = 0  # reviews spent on this scene
        # The valid scene a critic asked to revise: kept if the revision does not come out better.
        reviewed: tuple[dict[str, Any], CompileResult] | None = None
        reviewed_majors = 0  # the major problems the critic found in it
        planned = self._planned(gen)
        plan: Any = None  # the planned flow's stage plan (ADR-047), carried into corrections
        # Cost-saver lessons (ADR-055): the saved drawings this scene's brief names exactly.
        offered: dict[str, SavedObject] | None = None
        saved_text: str | None = None
        if self._costsaver(gen) and self.objects is not None:
            offers = self.objects.offers(gen, rec)
            offered = {o.obj.id: o.obj for o in offers}
            saved_text = offer_text(offers)
            rec.saved = {"offered": [o.obj.ref for o in offers], "used": []}

        for n in range(1, max_attempts + 1):
            final = n == max_attempts and n > 1
            kind = "plan" if n == 1 else ("repair" if llm_error and dsl is None else "correction")
            stage = "animation_planner" if n == 1 else ("correction_final" if final else "correction")
            rec.status = "generating" if n == 1 else "correcting"
            await changed()
            t0 = time.perf_counter()
            attempt = Attempt(n=n, kind=kind, stage=stage)
            count = self._draft_count(gen, n)
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
                            previous=self._previous(dsl, plan, planned, offered is not None),
                            issues=issues,
                            llm_error=llm_error,
                            critic_notes=critic_notes,
                            planned=planned,
                            saved=saved_text,
                        ),
                        rec,
                        prev_state,
                        self._relocating(gen),
                        planned,
                        offered,
                    )
                )
                for _ in range(count)
            }
            try:
                result, dsl, res, attempt.autofixed = await self._race(mine, rivals)
                if planned:
                    plan = result.data.get("stage") if isinstance(result.data, dict) else None
                    rec.layout_plan = self._plan_reports.pop(id(result), None) or rec.layout_plan
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
            # What is shown: the valid scene, framed by the camera (teaching lessons). Corrections
            # keep working on the scene as the model wrote it (`dsl`).
            shown, shown_res = dsl, res
            if res.ok and res.endState is not None and self._framing(gen):
                shown, shown_res = await self._frame(dsl, res, prev_state)
            if (
                res.ok
                and critic_rounds < self._critic_rounds(gen)
                and n < max_attempts
                and self._critics_on(gen)
            ):
                critic_rounds += 1
                report = await self._critique(gen, rec, shown, shown_res, previous_scenes, ctx, prev_state)
                attempt.critic = report.model_dump() if report else None
                major = [i for i in (report.issues if report else []) if i.severity == "major"]
                if report is not None and report.verdict == "revise" and major:
                    critic_notes = "\n".join(f"- {i.problem} -> {i.fix}" for i in major)
                    attempt.seconds = round(time.perf_counter() - t0, 2)
                    rec.attempts.append(attempt)
                    issues = []
                    if shown_res.endState is not None:
                        reviewed = (shown, shown_res)
                        reviewed_majors = len(major)
                    continue
            attempt.seconds = round(time.perf_counter() - t0, 2)
            rec.attempts.append(attempt)
            if res.ok and res.endState is not None:
                if reviewed is not None:
                    shown, shown_res = await self._second_look(
                        gen,
                        rec,
                        attempt,
                        (shown, shown_res),
                        reviewed,
                        reviewed_majors,
                        previous_scenes,
                        ctx,
                        prev_state,
                    )
                warnings = [i.model_dump() for i in shown_res.issues if i.severity == "warning"]
                gen.metrics.glitches_left += len(glitches_of(shown_res))
                self._accept(rec, shown, shown_res, warnings, "ready")
                await changed()
                return
            issues = errors + [w for w in warnings if w.get("source") == "layout"][:5]

        if reviewed is not None:
            # The critic's revision never became valid: the valid scene it reviewed is shown.
            dsl, res = reviewed
            warnings = [i.model_dump() for i in res.issues if i.severity == "warning"]
            gen.metrics.glitches_left += len(glitches_of(res))
            self._accept(rec, dsl, res, warnings, "ready")
            await changed()
            return

        if gen.teaching is not None and dsl is not None and issues:
            saved = salvage(dsl, [i for i in issues if i.get("severity") == "error"])
            if saved is not None:
                fixed, res, _ = await self._check(saved, prev_state)
                if res.ok and res.endState is not None:
                    log.info("scene %s: kept without the labels it could not stop overlapping", rec.id)
                    warnings = [i.model_dump() for i in res.issues if i.severity == "warning"]
                    gen.metrics.glitches_left += len(glitches_of(res))
                    self._accept(rec, fixed, res, warnings, "ready")
                    await changed()
                    return

        reason = llm_error or (
            format_issues(issues, limit=3) if issues else "the scene could not be generated"
        )
        await self._degrade(rec, prev_state, reason)
        gen.metrics.degraded += 1
        await changed()

    def _framing(self, gen: Generation) -> bool:
        return gen.teaching is not None and self.settings.animator_auto_frame

    async def _frame(
        self, dsl: dict[str, Any], res: CompileResult, prev_state: SceneState | None
    ) -> tuple[dict[str, Any], CompileResult]:
        """Auto-framing (ADR-044): the camera frames what the scene actually shows, like a camera
        operator. The engine reports where every object is over the whole scene; the camera then
        centres that content on the stage (between the title bar and the captions) and zooms in until
        it fills about 86% of it, at most 1.5x. Nothing moves relative to anything else, and text only
        gets larger. Scenes whose model chose a camera (a set camera or a camera move) are left alone,
        and the framed scene is kept only if it stays valid with no more glitches or untidiness."""
        if dsl.get("layout") in ("title_card", "no_title"):
            return dsl, res
        if any(a.get("action") == "camera_move" for a in dsl.get("timeline") or []):
            return dsl, res
        cam = dsl.get("camera") or {}
        center = cam.get("center") or {}
        if (
            abs(float(center.get("x", 800)) - 800) > 1
            or abs(float(center.get("y", 450)) - 450) > 1
            or abs(float(cam.get("zoom") or 1) - 1) > 1e-3
        ):
            return dsl, res
        duration = float(res.duration or dsl.get("duration") or 0)
        if duration <= 0.2:
            return dsl, res
        times = {round(duration * k / 12, 3) for k in range(1, 12)}
        times |= {round(max(0.05, b.end - 0.1), 3) for b in res.beats or []}
        try:
            sample = await self.engine.sample(dsl, sorted(t for t in times if 0 < t < duration), prev_state)
        except EngineError:
            return dsl, res
        boxes = [
            o["bbox"]
            for frame in sample.frames
            for o in frame.get("objects") or []
            if o.get("layer") != "overlay"
            and (o.get("alpha") or 0) > 0.05
            and isinstance(o.get("bbox"), dict)
            and (o["bbox"].get("w", 0) > 0 or o["bbox"].get("h", 0) > 0)
        ]
        if not sample.ok or not boxes:
            return dsl, res
        x0 = min(b["x"] for b in boxes)
        y0 = min(b["y"] for b in boxes)
        x1 = max(b["x"] + b["w"] for b in boxes)
        y1 = max(b["y"] + b["h"] for b in boxes)
        sx0, sy0, sx1, sy1 = STAGE
        zoom = min(
            FRAME_MAX_ZOOM,
            FRAME_FILL * (sx1 - sx0) / max(1.0, x1 - x0),
            FRAME_FILL * (sy1 - sy0) / max(1.0, y1 - y0),
        )
        if zoom < 1:
            return dsl, res  # it does not fit as it is: the engine's own checks deal with that
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        scx, scy = (sx0 + sx1) / 2, (sy0 + sy1) / 2
        if zoom < 1.08 and abs(cx - scx) < 60 and abs(cy - scy) < 40:
            return dsl, res  # already well framed
        framed = copy.deepcopy(dsl)
        framed["camera"] = {
            "center": {"x": round(cx - (scx - 800) / zoom, 1), "y": round(cy - (scy - 450) / zoom, 1)},
            "zoom": round(zoom, 3),
        }
        try:
            again = await self.engine.compile(framed, prev_state)
        except EngineError:
            return dsl, res
        if (
            again.ok
            and again.endState is not None
            and len(glitches_of(again)) <= len(glitches_of(res))
            and _untidy(again) <= _untidy(res)
        ):
            return framed, again
        return dsl, res

    def _shape(self, data: dict[str, Any], rec: SceneRecord) -> dict[str, Any]:
        return normalize(data, rec, independent=self.settings.animator_scene_mode == "parallel")

    async def _check(
        self,
        dsl: dict[str, Any],
        prev_state: SceneState | None,
        relocate: bool = False,
        plan: StagePlan | None = None,
        report: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], CompileResult, bool]:
        """Compile, then apply the sure fixes for its errors (pipeline/tidy.py) and compile again.
        The fixed scene is kept when it has fewer errors, or as many but none of the old ones: the
        engine checks layout only once the timing and references are right, so new layout errors
        are progress. Three passes at most (a fix can uncover the next one). With a stage plan (the
        planned flow, ADR-047), a valid scene is laid out by the planner before the polish."""
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
            # In the planned flow a draft left with layout errors only is progress: the planner
            # lays those out.
            onward = plan is not None and layout_only(res_fixed) and not layout_only(res)
            if (
                not res_fixed.ok
                and not onward
                and (len(after) > len(before) or (len(after) == len(before) and after & before))
            ):
                break
            dsl, res, autofixed = fixed, res_fixed, True
        if plan is not None and (res.ok or layout_only(res)):
            dsl, res = await self._plan_layout(
                dsl, res, plan, prev_state, report if report is not None else {}
            )
            if not res.ok and layout_only(res):
                dsl, res = await self._rescue_labels(
                    dsl, res, prev_state, report if report is not None else {}
                )
        if res.ok and self.settings.animator_layout_polish:
            dsl, res, polished = await self._polish(dsl, res, prev_state)
            autofixed = autofixed or polished
        # Relations last among the moves (ADR-048): nothing after them shifts what they placed.
        if plan is not None and (res.ok or layout_only(res)):
            dsl, res = await self._snap_relations(
                dsl, res, plan, prev_state, report if report is not None else {}
            )
        if res.ok and relocate and troubled_labels(res):
            dsl, res, moved = await self._relocate(dsl, res, prev_state)
            autofixed = autofixed or moved
        return dsl, res, autofixed

    async def _plan_layout(
        self,
        dsl: dict[str, Any],
        res: CompileResult,
        plan: StagePlan,
        prev_state: SceneState | None,
        report: dict[str, Any],
    ) -> tuple[dict[str, Any], CompileResult]:
        """The planned layout (ADR-047): the planner's candidates, best first, each kept only when the
        engine finds the scene valid with no more visual glitches than the scene as drawn. When none
        is, the scene stays as drawn (and the classic tidying follows)."""
        try:
            planned = await self.layout_planner.plan(dsl, plan, res, prev_state)
        except EngineError as e:
            report.update(applied=None, skipped=f"engine: {e}")
            return dsl, res
        report.update(planned.report)
        report["applied"] = None
        report["draft_valid"] = res.ok
        # A draft with layout errors takes the first candidate the engine finds valid; a valid one
        # only a candidate with no more visual glitches.
        before = planned_glitches(res, plan) if res.ok else 10**6
        # A broken draft is taken from the planner only clean: a messy one goes to a correction
        # round with the engine's exact errors instead.
        max_glitches = 10**6 if res.ok else PLANNED_RESCUE_GLITCHES
        tried: dict[str, str] = {}
        for cand in planned.candidates:
            if cand.least < PLANNED_LEAST_SCALE:
                # It only fits by making things small: the scene goes on as drawn (a correction
                # round, when it has errors, does better than a tiny picture). Anything else the
                # engine judges (valid, no more glitches, text readable).
                tried[cand.name] = f"does not fit unless things shrink to {cand.least}x"
                continue
            try:
                again = await self.engine.compile(cand.dsl, prev_state)
            except EngineError:
                tried[cand.name] = "engine error"
                continue
            errors = [i for i in again.issues if i.severity == "error"]
            after = planned_glitches(again, plan)
            if again.ok and again.endState is not None and after <= min(before, max_glitches):
                report.update(
                    applied=cand.name, glitches=[min(before, planned_glitches(res, plan)), after], tried=tried
                )
                return cand.dsl, again
            tried[cand.name] = (
                f"{errors[0].code}: {errors[0].message[:120]}" if errors else f"glitches {before} -> {after}"
            )
        report["tried"] = tried
        return dsl, res

    async def _snap_relations(
        self,
        dsl: dict[str, Any],
        res: CompileResult,
        plan: StagePlan,
        prev_state: SceneState | None,
        report: dict[str, Any],
    ) -> tuple[dict[str, Any], CompileResult]:
        """Relations (ADR-048): what the plan says sits on, in or along what is checked with the
        engine's geometry at every moment it should hold, and snapped right where it does not. A snap
        is kept when the relations are closer to holding, the scene is no less valid and shows no more
        visual glitches (contact the relations mean is not a glitch). What stays broken is reported
        precisely, for the reviewer and a correction round."""
        out: dict[str, Any] = {}

        async def frames_of(scene: dict[str, Any], comp: dict[str, Any] | None) -> list[dict[str, Any]]:
            duration = float((comp or {}).get("duration") or scene.get("duration") or 0)
            sample = await self.engine.sample(scene, sample_times(duration, comp), prev_state, lenient=True)
            return sample.frames

        def glitches(r: CompileResult) -> int:
            return sum(1 for i in glitches_of(r) if not intended(i.message, rels))

        try:
            compiled = res.compiled or (await self.engine.compile(dsl, prev_state, lenient=True)).compiled
            if compiled is None:
                return dsl, res
            frames = await frames_of(dsl, compiled)
            # Vehicles on a road the plan forgot; a subject naming several separate things is each.
            extra = implied(dsl, plan.relations, frames)
            rels = expand(dsl, resolve_kinds(dsl, [*plan.relations, *extra], frames), frames)
            if not rels:
                return dsl, res
            report["relations"] = out
            out["checked"] = len(rels)
            if extra:
                out["implied"] = [f"{r.subject} {r.relation} {r.object}" for r in extra]
            verdict = verify(dsl, rels, frames, compiled)
            out["broken"] = [describe(c) for c in verdict.broken()]
            snaps: list[str] = []

            async def attempt(v: Verdict) -> bool:
                """Snap what `v` finds broken; kept when the relations are closer to holding, the scene
                no less valid and with no more visual glitches."""
                nonlocal dsl, res, verdict, frames, compiled
                cand, done = snap(dsl, rels, v, frames, compiled)
                if not done:
                    return False
                again = await self.engine.compile(cand, prev_state)
                if not (again.ok or (not res.ok and layout_only(again))):
                    return False
                comp = again.compiled or (await self.engine.compile(cand, prev_state, lenient=True)).compiled
                f2 = await frames_of(cand, comp)
                v2 = verify(cand, rels, f2, comp)
                worse = again.ok and res.ok and glitches(again) > glitches(res)
                if v2.total >= verdict.total - 1 or worse:
                    return False
                dsl, res, verdict, frames, compiled = cand, again, v2, f2, comp
                snaps.extend(done)
                return True

            for _ in range(RELATION_PASSES):
                if not verdict.broken():
                    break
                if await attempt(verdict):
                    continue
                # Together they did not help (one snap pushed a thing into another): one at a time.
                singles = [n for n, c in verdict.worst.items() if c.size > TOLERANCE][:RELATION_SINGLES]
                progress = False
                for n in singles if len(singles) > 1 else []:
                    if n in verdict.worst and verdict.worst[n].size > TOLERANCE:
                        progress = await attempt(verdict.only(n)) or progress
                if not progress:
                    break
        except EngineError as e:
            out["error"] = str(e)[:200]
            return dsl, res
        if snaps:
            out["snapped"] = snaps
        out["unresolved"] = [describe(c) for c in verdict.broken(RELATION_REPORT_PX) if worth_reporting(c)]
        return dsl, res

    async def _rescue_labels(
        self, dsl: dict[str, Any], res: CompileResult, prev_state: SceneState | None, report: dict[str, Any]
    ) -> tuple[dict[str, Any], CompileResult]:
        """The planned flow (ADR-047): labels the planner could not separate (two labels inside one
        drawing) are moved like a covered label (ADR-046), the later of each pair first. A move is
        kept when the scene has fewer errors and no new kind of error; no model call."""
        budget = RELOCATE_COMPILES
        moved: list[str] = []
        for _ in range(RELOCATE_LABELS):
            pairs = [
                ids
                for i in res.issues
                if i.severity == "error"
                and i.code == "LABEL_OVERLAP"
                and len(ids := _QUOTED_IDS.findall(i.message)) >= 2
            ]
            if not pairs or budget <= 0:
                break
            improved = False
            for label in (pairs[0][1], pairs[0][0]):
                for place in (await self._label_places(dsl, res, label, prev_state, lenient=True))[
                    :RELOCATE_TRIES
                ]:
                    if budget <= 0:
                        break
                    budget -= 1
                    trial = copy.deepcopy(dsl)
                    obj = next((o for o in trial.get("objects") or [] if o.get("id") == label), None)
                    if obj is None:
                        break
                    obj["place"] = place
                    try:
                        again = await self.engine.compile(trial, prev_state)
                    except EngineError:
                        continue
                    if again.ok or (layout_only(again) and _count(again) < _count(res)):
                        dsl, res, improved = trial, again, True
                        moved.append(label)
                        break
                if improved:
                    break
            if not improved or res.ok:
                break
        if moved:
            report["rescued_labels"] = moved
        return dsl, res

    async def _polish(
        self, dsl: dict[str, Any], res: CompileResult, prev_state: SceneState | None
    ) -> tuple[dict[str, Any], CompileResult, bool]:
        """Tidy a valid scene's layout with the engine's hints, without a model call: a label moved off
        the object it straddles, content lifted from behind the caption, an arrow bent around what it
        ran through, characters the font cannot draw removed. A change is kept only when the scene
        stays valid and has fewer such problems. Two passes at most (a move can reveal the next)."""
        polished = False
        for _ in range(2):
            hinted = [
                i.model_dump()
                for i in res.issues
                if i.severity == "warning" and (i.fix is not None or i.code == "UNSUPPORTED_GLYPH")
            ]
            if not hinted:
                break
            fixed = autofix(dsl, hinted)
            if fixed is None:
                break
            res_fixed = await self.engine.compile(fixed, prev_state)
            if not res_fixed.ok or _untidy(res_fixed) >= _untidy(res):
                break
            dsl, res, polished = fixed, res_fixed, True
        return dsl, res, polished

    async def _relocate(
        self, dsl: dict[str, Any], res: CompileResult, prev_state: SceneState | None
    ) -> tuple[dict[str, Any], CompileResult, bool]:
        """Label relocation (ADR-046), after the polish, without a model call. A label (or note, badge,
        caption text) that still covers an object, hides under one or has a connector across it is
        tried in other places around what it names: the other sides of its reference at a few
        distances, around the whole drawing when it names a part of one (a bunch of grapes made of
        circles), or nearby when it was placed by coordinates. The engine judges every try; the first
        place that clears the label without any other problem getting worse is kept."""
        moved = False
        budget = RELOCATE_COMPILES
        for label in troubled_labels(res)[:RELOCATE_LABELS]:
            if budget <= 0:
                break
            places = await self._label_places(dsl, res, label, prev_state)
            for place in places[:RELOCATE_TRIES]:
                if budget <= 0:
                    break
                budget -= 1
                trial = copy.deepcopy(dsl)
                obj = next((o for o in trial.get("objects") or [] if o.get("id") == label), None)
                if obj is None:
                    break
                obj["place"] = place
                try:
                    again = await self.engine.compile(trial, prev_state)
                except EngineError:
                    continue
                if (
                    again.ok
                    and again.endState is not None
                    and _trouble_with(again, label) == 0
                    and _untidy(again) < _untidy(res)
                    and len(glitches_of(again)) <= len(glitches_of(res))
                ):
                    dsl, res, moved = trial, again, True
                    break
        return dsl, res, moved

    async def _label_places(
        self,
        dsl: dict[str, Any],
        res: CompileResult,
        label: str,
        prev_state: SceneState | None,
        lenient: bool = False,
    ) -> list[dict[str, Any]]:
        """Places to try for a label, the most natural first: the other sides of what it is placed
        against (a little farther each time), the sides of the whole drawing when that is a part of
        one, then nearby positions when it was placed by coordinates. A label meant to be inside
        something (placed at its centre or inside it, or lying within a much larger object: the
        address on a browser bar, a heading in a card) is left where it is."""
        objects = {str(o.get("id")): o for o in dsl.get("objects") or []}
        obj = objects.get(label)
        if obj is None or not isinstance(obj.get("place"), dict):
            return []
        place = obj["place"]
        ref = place.get("relativeTo")
        if ref and str(place.get("side", "")).startswith(("center", "inside")):
            return []
        times = sorted(
            {round(i.time, 3) for i in res.issues if i.time is not None and f"'{label}'" in i.message}
        )
        if not times:
            return []
        try:
            sample = await self.engine.sample(dsl, times[:1], prev_state, lenient=lenient)
        except EngineError:
            return []
        if not sample.frames or (not sample.ok and not lenient):
            return []
        boxes = {
            o["id"]: o["bbox"]
            for o in sample.frames[0].get("objects") or []
            if isinstance(o.get("bbox"), dict)
        }
        own = boxes.get(label)
        if own is None or _inside_a_frame(label, own, boxes):
            return []
        out: list[dict[str, Any]] = []
        if isinstance(ref, str) and ref in objects:
            for gap in (24, 44, 72):
                for side in ("below", "above", "right", "left"):
                    if side == place.get("side") and gap <= float(place.get("gap") or 24):
                        continue
                    out.append(
                        {"relativeTo": ref, "side": side, "gap": gap, "align": "center", "follow": True}
                    )
        w, h = own["w"], own["h"]
        drawing = _drawing_of(str(ref or ""), boxes)
        if drawing is not None:
            x0, y0, x1, y1 = drawing
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            for gap in (24, 48):
                out += [
                    {"x": round(cx, 1), "y": round(y1 + gap + h / 2, 1)},
                    {"x": round(cx, 1), "y": round(y0 - gap - h / 2, 1)},
                    {"x": round(x1 + gap + w / 2, 1), "y": round(cy, 1)},
                    {"x": round(x0 - gap - w / 2, 1), "y": round(cy, 1)},
                ]
        if place.get("x") is not None and place.get("y") is not None:
            x, y = float(place["x"]), float(place["y"])
            for r in (0.5, 0.9):
                for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (-1, 1), (1, -1), (-1, -1)):
                    out.append({"x": round(x + dx * r * w, 1), "y": round(y + dy * r * h * 1.6, 1)})
        return out

    async def _draft(
        self,
        call: Awaitable[LLMResult],
        rec: SceneRecord,
        prev_state: SceneState | None,
        relocate: bool = False,
        planned: bool = False,
        offered: dict[str, SavedObject] | None = None,
    ) -> Draft:
        result = await call
        if not planned:
            return (result, *await self._check(self._shape(result.data, rec), prev_state, relocate))
        # The planned flow (ADR-047): the stage plan rides with the reply; the scene itself is the
        # engine's (the plan is never part of what is compiled or played).
        data = dict(result.data) if isinstance(result.data, dict) else {}
        plan = parse_plan(data.pop("stage", None))
        report: dict[str, Any] = {}
        # Cost-saver lessons (ADR-055): each saved drawing the reply names becomes its drawing.
        saved = resolve_saved(data, offered) if offered is not None else None
        if saved is not None:
            data = saved.dsl
            if saved.used:
                report["saved"] = saved.used
            if saved.adjusted:
                report["saved_entrances"] = saved.adjusted
        shaped, repaired = repair_draft(self._shape(data, rec))
        if repaired:
            report["repaired"] = repaired
        checked = await self._check(shaped, prev_state, relocate, plan, report)
        if saved is not None and saved.problems:
            report["saved_problems"] = [p["message"] for p in saved.problems]
            checked = (checked[0], _with_problems(checked[1], saved), checked[2])
        if len(self._plan_reports) > 256:  # reports of drafts that lost a race are never collected
            self._plan_reports.pop(next(iter(self._plan_reports)))
        self._plan_reports[id(result)] = report
        return (result, *checked)

    def _relocating(self, gen: Generation) -> bool:
        """Label relocation (ADR-046) for teaching lessons; the fast pipeline keeps its polish only."""
        return gen.teaching is not None and self.settings.animator_label_relocation

    def _planned(self, gen: Generation) -> bool:
        """The planned layout flow (ADR-047): teaching lessons whose layout mode is planned (the
        lesson's own choice, else ANIMATOR_LAYOUT_MODE), cost-saver ones included (ADR-055).
        Everything else is laid out the classic way."""
        mode = gen.request.layout or self.settings.animator_layout_mode
        return gen.teaching is not None and mode in ("planned", "costsaver")

    def _costsaver(self, gen: Generation) -> bool:
        """Cost-saver lessons (ADR-055): planned ones whose scenes may place saved drawings. With the
        object library off, they are made exactly as planned lessons."""
        mode = gen.request.layout or self.settings.animator_layout_mode
        return gen.teaching is not None and mode == "costsaver" and self.objects is not None

    def library_note(self, gen: Generation, script: TeachingScript) -> str | None:
        """For a cost-saver lesson's visual director (ADR-055): the saved drawings its script names, so
        the scenes are planned around them, in their views, the same in every scene. None otherwise."""
        if not self._costsaver(gen) or self.objects is None:
            return None
        lines = [f"{sc.title}. {sc.goal}." for sc in script.scenes]
        lines += [f"{b.say} {b.show}" for sc in script.scenes for b in sc.beats]
        lesson = [gen.request.topic, script.title, script.core_idea, script.intuition]
        found = self.objects.offers_text(lines, lesson, limit=8)
        if not found:
            return None
        return (
            "Saved drawings this lesson can use (detailed, ready-made): where a scene shows one of these "
            "things, plan it as that drawing, in one of its views, and name the view in the object's "
            "description (for example 'heart: the human heart, cut open with its four chambers'). The same "
            "thing keeps the same drawing in every scene; its colours are its own. Draw anything else as "
            "usual.\n" + found
        )

    @staticmethod
    def _previous(
        dsl: dict[str, Any] | None, plan: Any, planned: bool, costsaver: bool
    ) -> dict[str, Any] | None:
        """The scene a correction starts from: as drafted, with its stage plan (planned flow), and its
        saved drawings back as the short references the writer used (cost saver)."""
        if dsl is None or not planned:
            return dsl
        previous = {**dsl, "stage": plan}
        return compact_saved(previous) if costsaver else previous

    async def _saved_used(self, gen: Generation, rec: SceneRecord) -> None:
        """Record the saved drawings a finished scene shows (ADR-055)."""
        refs = placed_refs(rec.dsl) if rec.status == "ready" else []
        rec.saved = {**(rec.saved or {}), "used": refs}
        gen.metrics.saved_objects = sum(len((s.saved or {}).get("used") or []) for s in gen.scenes)
        if refs and self.objects is not None:
            await self.objects.record_uses(gen.id, rec.id, refs)

    def _draft_count(self, gen: Generation, attempt: int) -> int:
        if self._planned(gen):
            s = self.settings
            return s.animator_planned_scene_drafts if attempt == 1 else s.animator_planned_correction_drafts
        return (
            self.settings.animator_scene_drafts if attempt == 1 else self.settings.animator_correction_drafts
        )

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

    def _visual_critic(self, gen: Generation) -> bool:
        """Rendered keyframes go to a vision model: for every lesson (ANIMATOR_ENABLE_VISUAL_CRITIC)
        or for teaching lessons (ANIMATOR_TEACHING_VISUAL_CRITIC, ADR-044)."""
        wanted = self.settings.animator_enable_visual_critic or (
            gen.teaching is not None and self.settings.animator_teaching_visual_critic
        )
        return wanted and self.keyframes is not None

    async def _second_look(
        self,
        gen: Generation,
        rec: SceneRecord,
        attempt: Any,
        revised: tuple[dict[str, Any], CompileResult],
        reviewed: tuple[dict[str, Any], CompileResult],
        reviewed_majors: int,
        previous_scenes: list[dict[str, Any]],
        ctx: CallContext,
        prev_state: SceneState | None,
    ) -> tuple[dict[str, Any], CompileResult]:
        """The scene a revision asked for by the critic leaves behind (ADR-061): the critic looks once
        more (one call, no further fixes) and the revision is kept unless it came out worse than the
        scene it reviewed (more major problems; as many and more visual glitches). Without that look,
        only the glitches decide, as before."""
        more_glitches = len(glitches_of(revised[1])) > len(glitches_of(reviewed[1]))
        report = None
        if self.settings.animator_critic_second_look and self._critics_on(gen):
            report = await self._critique(gen, rec, revised[0], revised[1], previous_scenes, ctx, prev_state)
            gen.metrics.second_looks += 1
        if report is None:
            # The revision added visual glitches: the scene the critic saw stays.
            return reviewed if more_glitches else revised
        attempt.critic = report.model_dump()
        majors = len([i for i in report.issues if i.severity == "major"]) if report.verdict == "revise" else 0
        if majors > reviewed_majors or (majors == reviewed_majors and more_glitches):
            gen.metrics.revisions_turned_down += 1
            log.info(
                "scene %s: the revision came out worse (%d > %d major problems); kept the one reviewed",
                rec.id,
                majors,
                reviewed_majors,
            )
            return reviewed
        return revised

    def _critic_rounds(self, gen: Generation) -> int:
        """How many times a scene may be reviewed (each review can ask for one round of fixes)."""
        return max(1, self.settings.animator_teaching_critic_rounds) if gen.teaching is not None else 1

    def _critics_on(self, gen: Generation) -> bool:
        return self.settings.animator_enable_llm_critic or self._visual_critic(gen)

    async def _critique(
        self,
        gen: Generation,
        rec: SceneRecord,
        dsl: dict[str, Any],
        res: CompileResult,
        previous_scenes: list[dict[str, Any]],
        ctx: CallContext,
        prev_state: SceneState | None = None,
    ) -> Any:
        stages = await self.stages()
        images: list[bytes] = []
        summary = summarize_for_critic(dsl, res)
        poor: list[str] = []
        planned = self._planned(gen)
        if self._visual_critic(gen) and self.keyframes is not None and res.beats:
            k = self.settings.animator_visual_critic_keyframes
            ends = [b.end - 0.1 for b in res.beats]
            step = max(1, len(ends) // k)
            times = [max(0.1, t) for t in ends[::step][:k]]
            images = await self.keyframes.keyframes([*previous_scenes, dsl], len(previous_scenes), times)
            if gen.teaching is not None:
                measured, poor = await self._composition(dsl, prev_state, times)
                if measured:
                    summary += "\nComposition, measured by the engine at the keyframes:\n" + "\n".join(
                        measured
                    )
        try:
            report = await stages.critic(gen, rec, summary, images, ctx, planned=planned)
        except (LLMRefusal, LLMIncomplete, LLMBadOutput):
            report = None  # a failed review never blocks a valid scene
        broken = ((rec.layout_plan or {}).get("relations") or {}).get("unresolved") or [] if planned else []
        if broken:
            # What the plan says sits on, in or along what, and still does not (ADR-048): always fixed.
            issue = CriticIssue(
                severity="major",
                problem="Things are not where the scene's plan says they are: " + " ".join(broken[:4]),
                fix="Place each of these so it sits exactly where the plan says (on the surface, inside "
                "the container, along the line), and make its moves end there; keep everything else.",
            )
            issues = [*(report.issues if report else []), issue]
            report = CriticReport(verdict="revise", issues=issues)
        if poor:
            # A picture that is small or off-centre with most of the stage empty is always fixed,
            # whatever the reviewer saw (Code2Video's occupancy feedback).
            issue = CriticIssue(
                severity="major",
                problem="The composition is poor: " + "; ".join(poor) + ".",
                fix="At every moment, make the visible picture one balanced group centred on the stage "
                "that fills about 60 to 80% of it: enlarge the main objects (sizes, widths, icon sizes) "
                "and place them around the stage centre (slots stage.center, stage.left, stage.right), "
                "keeping labels next to what they name.",
            )
            if report is None:
                report = CriticReport(verdict="revise", issues=[issue])
            else:
                report = CriticReport(verdict="revise", issues=[*report.issues, issue])
        return report

    async def _composition(
        self, dsl: dict[str, Any], prev_state: SceneState | None, times: list[float]
    ) -> tuple[list[str], list[str]]:
        """Where the visible picture sits on screen at each time (the camera applied): its box, how
        much of the stage it covers and how far its centre is from the stage centre. Returns the
        lines for the reviewer and the times whose composition is poor."""
        try:
            sample = await self.engine.sample(dsl, times, prev_state)
        except EngineError:
            return [], []
        if not sample.ok:
            return [], []
        sx0, sy0, sx1, sy1 = STAGE
        sw, sh = sx1 - sx0, sy1 - sy0
        lines: list[str] = []
        poor: list[str] = []
        for frame in sample.frames:
            cam = frame.get("camera") or {}
            zoom = float(cam.get("zoom") or 1)
            cx, cy = float(cam.get("cx", 800)), float(cam.get("cy", 450))
            boxes = [
                o["bbox"]
                for o in frame.get("objects") or []
                if o.get("layer") != "overlay"
                and (o.get("alpha") or 0) > 0.05
                and isinstance(o.get("bbox"), dict)
            ]
            if not boxes:
                continue
            x0 = (min(b["x"] for b in boxes) - cx) * zoom + 800
            x1 = (max(b["x"] + b["w"] for b in boxes) - cx) * zoom + 800
            y0 = (min(b["y"] for b in boxes) - cy) * zoom + 450
            y1 = (max(b["y"] + b["h"] for b in boxes) - cy) * zoom + 450
            wf, hf = (x1 - x0) / sw, (y1 - y0) / sh
            dx, dy = ((x0 + x1) / 2 - (sx0 + sx1) / 2) / sw, ((y0 + y1) / 2 - (sy0 + sy1) / 2) / sh
            t = float(frame.get("t", 0))
            line = (
                f"- at {t:.1f}s: the picture spans x {x0:.0f} to {x1:.0f}, y {y0:.0f} to {y1:.0f} "
                f"({wf:.0%} of the stage width, {hf:.0%} of its height); its centre is "
                f"{dx:+.0%} across and {dy:+.0%} down from the stage centre"
            )
            if wf * hf < 0.25 or abs(dx) > 0.2 or abs(dy) > 0.2:
                where = "low" if dy > 0.2 else "high" if dy < -0.2 else "left" if dx < -0.2 else "right"
                problem = (
                    f"at {t:.1f}s the picture covers only {wf:.0%} x {hf:.0%} of the stage"
                    if wf * hf < 0.25
                    else f"at {t:.1f}s the picture sits {where} on the stage (x {x0:.0f}-{x1:.0f}, "
                    f"y {y0:.0f}-{y1:.0f}), leaving the rest empty"
                )
                poor.append(problem)
                line += " (POOR)"
            lines.append(line)
        return lines, poor

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
