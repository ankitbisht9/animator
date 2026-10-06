"""Doubts (ADR-050): a learner raises a hand mid-lesson, asks, and the tutor answers in one model call.

`ask`:
1. The lesson and the moment: what each scene said and showed, where the learner is, what is on
   screen right now (the engine's state at that time) and what was being said.
2. One model call (doubts/prompts/doubt.v1.md): the kind of doubt, how deep, the sentences (and for
   Hinglish how the voice says them), what to point at, a picture or a board.
3. Tidied by code: the answer fits the time it may take (a share of the lesson's length), notes are
   short, a Hinglish voice line that does not match its caption is read as written.
4. The picture by code (doubts/visuals.py): the lesson's own picture with the things talked about
   glowing, or a board; the first variant the engine compiles is kept.
5. The voice: one take, the scene fitted to it, word-timed captions (the lesson's own narration code).

Nothing here changes a lesson: doubts are stored next to it (`<lesson>/doubts/<id>.json`) and read only
what the lesson already has. A doubt continues (follow-ups) with the conversation so far."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from animator.config import Settings
from animator.doubts.context import (
    Turn,
    answer_cap,
    lesson_block,
    lesson_seconds,
    playable,
    scene_duration,
    turn_block,
    words_for,
)
from animator.doubts.models import AskRequest, DoubtAnswer, DoubtBeat
from animator.doubts.phrases import phrases_for
from animator.doubts.visuals import board_variants, brief_glows, centre_column, picture_variants, short
from animator.engine_client import EngineClient, EngineError
from animator.jobs.store import GenerationStore
from animator.llm import LLMClient, LLMError
from animator.llm.types import CallContext, CallRecord, LLMRequest, Message
from animator.pipeline.models import Generation
from animator.pipeline.narration import floors, retime, speak_scene, split_take, take_needs, take_record
from animator.pipeline.schemas import strict_schema
from animator.tts import SpeechCache, caption_words, speech_text
from animator.tts.prosody import Delivery
from animator.tts.script import Script
from animator_contracts.protocol import CompileResult, SceneState

log = logging.getLogger("animator.doubts")

PROMPT_DIR = Path(__file__).parent / "prompts"
PROMPT_VERSION = "doubt.v1"
SCHEMA = strict_schema(DoubtAnswer)
OVER = 1.25  # an answer may run this much over its word budget before sentences are dropped
END_PAUSE_S = 0.6  # a moment of quiet after the answer's last word
MAX_OUTPUT_TOKENS = 8000


class DoubtError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class DoubtSessions:
    """Doubts of a lesson, stored next to it (files) or in memory (tests, memory storage)."""

    def __init__(self, root: Path | None) -> None:
        self.root = root
        self._memory: dict[str, dict[str, dict[str, Any]]] = {}

    def _dir(self, gen_id: str) -> Path:
        assert self.root is not None
        return self.root / "generations" / gen_id / "doubts"

    def load(self, gen_id: str, sid: str) -> dict[str, Any] | None:
        if self.root is None:
            return copy.deepcopy(self._memory.get(gen_id, {}).get(sid))
        try:
            data: dict[str, Any] = json.loads((self._dir(gen_id) / f"{sid}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data

    def save(self, gen_id: str, data: dict[str, Any]) -> None:
        if self.root is None:
            self._memory.setdefault(gen_id, {})[data["id"]] = copy.deepcopy(data)
            return
        d = self._dir(gen_id)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{data['id']}.json.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(d / f"{data['id']}.json")

    def all(self, gen_id: str) -> list[dict[str, Any]]:
        if self.root is None:
            return list(self._memory.get(gen_id, {}).values())
        out = []
        for p in sorted(self._dir(gen_id).glob("d_*.json")) if self._dir(gen_id).is_dir() else []:
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out

    def spent(self, gen_id: str) -> float:
        return sum(float(t.get("cost_usd") or 0) for s in self.all(gen_id) for t in s.get("turns") or [])


class DoubtService:
    def __init__(
        self,
        engine: EngineClient,
        llm: LLMClient,
        settings: Settings,
        speech: SpeechCache | None,
        store: GenerationStore,
    ) -> None:
        self.engine = engine
        self.llm = llm
        self.settings = settings
        self.speech = speech
        self.store = store
        self.sessions = DoubtSessions(
            settings.resolve(settings.animator_data_dir)
            if settings.animator_storage_backend == "files"
            else None
        )
        self._instructions: str | None = None
        self._icons: set[str] = set()
        self._phrases: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._transcriber: Any = None

    # ------------------------------------------------------------------ the model

    def models(self) -> list[str]:
        s = self.settings
        primary = (
            s.animator_model_doubt.strip()
            or s.animator_model_teacher.strip()
            or s.animator_model_storyboard.strip()
        )
        chain = [primary] if primary else []
        for m in s.animator_model_fallbacks.split(","):
            if m.strip() and m.strip() not in chain:
                chain.append(m.strip())
        if not chain:
            raise DoubtError(503, "No model is configured for doubts (ANIMATOR_MODEL_DOUBT).")
        return chain

    async def instructions(self) -> str:
        if self._instructions is None:
            schema = await self.engine.schema("llm")
            icons = ((schema.get("$defs") or {}).get("IconName") or {}).get("enum") or []
            self._icons = {str(i) for i in icons}
            text = (PROMPT_DIR / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")
            self._instructions = text.replace("{ICONS}", " ".join(sorted(self._icons)))
        return self._instructions

    # ------------------------------------------------------------------ a question

    async def ask(self, gen_id: str, req: AskRequest) -> dict[str, Any]:
        started = time.perf_counter()
        gen = await self.store.get(gen_id)
        if gen is None:
            raise DoubtError(404, "No such lesson.")
        session = self.sessions.load(gen_id, req.session_id) if req.session_id else None
        if req.session_id and session is None:
            raise DoubtError(404, "No such doubt.")
        k = int(session["scene_index"]) if session else req.scene_index
        if k >= len(gen.scenes) or not playable(gen.scenes[k]):
            raise DoubtError(409, "This part of the lesson is not ready yet.")
        budget = self.settings.animator_doubt_budget_usd
        if budget > 0 and self.sessions.spent(gen_id) >= budget:
            raise DoubtError(
                429, "This lesson's doubts have used up their budget (ANIMATOR_DOUBT_BUDGET_USD)."
            )
        if session is None:
            session = {
                "id": f"d_{uuid.uuid4().hex[:12]}",
                "generation_id": gen_id,
                "scene_index": k,
                "t": round(min(req.t, scene_duration(gen.scenes[k])), 3),
                "created_at": time.time(),
                "turns": [],
            }
        turns: list[dict[str, Any]] = session["turns"]
        if len(turns) >= self.settings.animator_doubt_max_turns:
            raise DoubtError(429, "This doubt has had many answers. Close it and raise a new one.")

        rec = gen.scenes[k]
        t = float(session["t"])
        prev = self._prev_state(gen, k)
        try:
            comp = await self.engine.compile(rec.dsl or {}, prev)
            at = await self.engine.state_at(rec.dsl or {}, t, prev)
        except EngineError as e:
            raise DoubtError(503, f"The engine could not open this moment of the lesson: {e}") from e
        now = at.state.model_dump(mode="json") if at.state is not None else None
        beats = list(comp.beats or [])
        saying = next((b.text for b in beats if b.start <= t < b.end), beats[-1].text if beats else None)
        cap = answer_cap(
            lesson_seconds(gen),
            self.settings.animator_doubt_share,
            self.settings.animator_doubt_min_s,
            self.settings.animator_doubt_max_s,
        )
        history = [Turn(x["question"], list(x["sentences"])) for x in turns]
        answer, records = await self._answer(
            gen, session["id"], len(turns) + 1, rec, k, t, saying, now, history, req.question, cap
        )
        answer = self._tidy(answer, gen, cap)

        scene_id = f"doubt_{session['id'][2:]}_{len(turns) + 1}"
        dsl, res, prev_state, mode, source = await self._picture(gen, answer, k, now, scene_id)
        narration = None
        if self.speech is not None and dsl is not None and res is not None:
            dsl, res, narration = await self._voice(gen, answer, dsl, res, prev_state)

        cost = sum(r.cost_usd or 0.0 for r in records)
        turn = {
            "n": len(turns) + 1,
            "question": req.question.strip(),
            "kind": answer.kind,
            "depth": answer.depth,
            "title": answer.title,
            "sentences": [b.text for b in answer.beats],
            "suggestion": answer.suggestion if answer.kind == "related" else None,
            "visual": {"mode": mode, "scene": source},
            "scene": dsl,
            "prev_state": prev_state.model_dump(mode="json") if prev_state is not None else None,
            "narration": narration,
            "duration": float(res.duration or 0) if res is not None else None,
            "cap_s": round(cap),
            "answer": answer.model_dump(mode="json"),
            "cost_usd": round(cost, 6),
            "model": records[-1].model if records else None,
            "latency_s": round(time.perf_counter() - started, 2),
            "calls": [r.to_json() for r in records],
            "at": time.time(),
        }
        turns.append(turn)
        self.sessions.save(gen_id, session)
        log.info(
            "doubt %s turn %d: %s/%s %s, %d sentences, %.1f s, $%.4f",
            session["id"],
            turn["n"],
            answer.kind,
            answer.depth,
            mode,
            len(answer.beats),
            turn["latency_s"],
            cost,
        )
        return {
            "session_id": session["id"],
            **{k2: v for k2, v in turn.items() if k2 not in ("answer", "calls")},
        }

    def get(self, gen_id: str, sid: str) -> dict[str, Any] | None:
        data = self.sessions.load(gen_id, sid)
        if data is None:
            return None
        data["turns"] = [{k: v for k, v in x.items() if k not in ("answer", "calls")} for x in data["turns"]]
        return data

    def _prev_state(self, gen: Generation, k: int) -> SceneState | None:
        if k == 0:
            return None
        before = gen.scenes[k - 1].end_state
        return SceneState.model_validate(before) if before else None

    async def _answer(
        self,
        gen: Generation,
        sid: str,
        n: int,
        rec: Any,
        k: int,
        t: float,
        saying: str | None,
        now: dict[str, Any] | None,
        history: list[Turn],
        question: str,
        cap: float,
    ) -> tuple[DoubtAnswer, list[CallRecord]]:
        records: list[CallRecord] = []
        request = LLMRequest(
            stage="doubt",
            prompt_version=PROMPT_VERSION,
            instructions=await self.instructions(),
            # The lesson first (the same for every doubt in it: cached), then this question.
            messages=[
                Message.user(lesson_block(gen)),
                Message.user(turn_block(gen, k + 1, rec, t, saying, now, history, question, cap)),
            ],
            schema_name="DoubtAnswer",
            schema=SCHEMA,
            models=self.models(),
            effort=self.settings.animator_effort_doubt,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            generation_id=gen.id,
            label=f"doubt {sid} turn {n}",
            cache_key=f"animator-doubt-{gen.id}",
        )
        ctx = CallContext(on_record=records.append)
        last: Exception | None = None
        # One call. Only an unusable reply (rare with a strict schema) is asked for again, once.
        for _ in range(2):
            try:
                result = await self.llm.generate(request, ctx)
                return DoubtAnswer.model_validate(result.data), records
            except (ValidationError, LLMError) as e:
                last = e
                if isinstance(e, LLMError) and getattr(e, "kind", "") in ("auth", "budget", "unavailable"):
                    break
        log.warning("doubt %s: no usable answer (%s)", sid, last)
        raise DoubtError(502, "The tutor could not answer just now. Please ask again.")

    def _tidy(self, answer: DoubtAnswer, gen: Generation, cap: float) -> DoubtAnswer:
        """Fit the answer to the time it may take (whole sentences), keep notes short, and read a
        Hinglish voice line as written when it does not match its caption word for word."""
        hinglish = gen.request.language.strip().lower() == "hinglish"
        limit = words_for(cap) * OVER
        kept: list[DoubtBeat] = []
        words = 0
        for b in answer.beats:
            n = len(caption_words(b.text))
            if kept and words + n > limit:
                break
            words += n
            spoken = b.spoken if hinglish and b.spoken and len(b.spoken) == n else None
            kept.append(b.model_copy(update={"spoken": spoken, "note": short(b.note)}))
        visual = answer.visual
        if answer.kind == "unrelated" and visual.mode == "scene":
            visual = visual.model_copy(update={"mode": "board", "scene": None})
        return answer.model_copy(update={"beats": kept, "visual": visual})

    # ------------------------------------------------------------------ the picture

    async def _picture(
        self, gen: Generation, answer: DoubtAnswer, k: int, now: dict[str, Any] | None, scene_id: str
    ) -> tuple[dict[str, Any] | None, CompileResult | None, SceneState | None, str, int | None]:
        """The answer's scene, the engine's result, the state it continues from, and which picture
        (scene mode: the lesson scene's number)."""
        await self.instructions()  # the icon names
        if answer.visual.mode == "scene":
            number = answer.visual.scene or (k + 1)
            j = number - 1 if 0 <= number - 1 <= k and playable(gen.scenes[number - 1]) else k
            state = now if j == k else gen.scenes[j].end_state
            if state:
                prev_state = SceneState.model_validate(state)
                source = gen.scenes[j].dsl or {}
                for dsl in picture_variants(answer, state, source, scene_id):
                    res = await self._compile(dsl, prev_state)
                    if res is not None:
                        return dsl, res, prev_state, "scene", j + 1
        for dsl in board_variants(answer, scene_id, self._icons):
            res = await self._compile(dsl, None)
            if res is None:
                continue
            if res.endState is not None:
                moved = centre_column(copy.deepcopy(dsl), res.endState.model_dump(mode="json"))
                again = await self._compile(moved, None) if moved is not None else None
                if moved is not None and again is not None:
                    dsl, res = moved, again
            return dsl, res, None, "board", None
        return None, None, None, "none", None

    async def _compile(self, dsl: dict[str, Any], prev: SceneState | None) -> CompileResult | None:
        try:
            res = await self.engine.compile(dsl, prev)
        except EngineError as e:
            log.warning("doubt scene %s: engine error %s", dsl.get("id"), e)
            return None
        if not res.ok:
            errors = [i.message for i in res.issues if i.severity == "error"][:2]
            log.info("doubt scene %s variant rejected: %s", dsl.get("id"), errors)
            return None
        return res

    # ------------------------------------------------------------------ the voice

    async def _voice(
        self,
        gen: Generation,
        answer: DoubtAnswer,
        dsl: dict[str, Any],
        res: CompileResult,
        prev: SceneState | None,
    ) -> tuple[dict[str, Any], CompileResult, dict[str, Any] | None]:
        """The answer spoken in one take, the scene fitted to the speech, word-timed captions. Any
        failure leaves captions only."""
        assert self.speech is not None
        try:
            language = gen.request.language
            voice, provider = self.speech.voice_for(language), self.speech.provider_for(language)
            beats = list(res.beats or [])
            texts = [b.text for b in beats]
            scripts: dict[str, Script] = {}
            for b in answer.beats:
                words = caption_words(b.text)
                if not words:
                    continue
                plain = speech_text(b.text).split(" ")
                spoken = tuple(b.spoken) if b.spoken else tuple(plain if len(plain) == len(words) else words)
                scripts[b.text.strip()] = Script(spoken, b.tone, deliveries=(Delivery(0, b.tone),))
            take = await speak_scene(self.speech, texts, voice, provider, scripts)
            if take is None:
                return dsl, res, None
            parts = split_take(beats, take)
            need = take_needs(parts, beats, END_PAUSE_S)
            fitted = retime(dsl, beats, need, floor=floors(dsl, beats))
            tries = [fitted, retime(dsl, beats, need)]
            if fitted is not None:
                tries.append(brief_glows(copy.deepcopy(fitted)))
            for retimed in tries:
                if retimed is None:
                    continue
                again = await self._compile(retimed, prev)
                if again is not None and again.beats:
                    dsl, res, beats = retimed, again, list(again.beats)
                    break
            return dsl, res, take_record(beats, parts, take, provider.name, voice)
        except Exception as e:
            log.warning("doubt voice failed: %s", e)
            return dsl, res, None

    # ------------------------------------------------------------------ around a doubt

    async def phrases(self, language: str) -> dict[str, list[dict[str, Any]]]:
        """The tutor's fixed lines for this language, each with its audio (spoken once, then cached)."""
        key = language.strip().lower() or "english"
        if key in self._phrases:
            return self._phrases[key]
        sets = phrases_for(language)
        out: dict[str, list[dict[str, Any]]] = {}
        if self.speech is None:
            out = {kind: [{"text": p.text, "audio": None} for p in lines] for kind, lines in sets.items()}
            self._phrases[key] = out
            return out
        speech = self.speech
        voice, provider = speech.voice_for(language), speech.provider_for(language)

        async def one(text: str, spoken: str) -> dict[str, Any]:
            try:
                s = await speech.speak(spoken, voice, provider)
                return {"text": text, "audio": s.file, "duration": round(s.duration_s, 2)}
            except Exception as e:
                log.warning("doubt phrase not spoken (%s): %s", text, e)
                return {"text": text, "audio": None}

        for kind, lines in sets.items():
            out[kind] = list(await asyncio.gather(*(one(p.text, p.spoken) for p in lines)))
        if all(x["audio"] for xs in out.values() for x in xs):
            self._phrases[key] = out  # retried next time while any is missing
        return out

    async def transcribe(self, audio: bytes, content_type: str, language: str) -> str:
        """Speech to text for browsers without speech recognition (OpenAI)."""
        s = self.settings
        if not s.has_openai_key or s.openai_api_key is None:
            raise DoubtError(503, "Speech input needs OPENAI_API_KEY.")
        if not audio:
            raise DoubtError(400, "No audio.")
        if self._transcriber is None:
            from openai import AsyncOpenAI

            self._transcriber = AsyncOpenAI(
                api_key=s.openai_api_key.get_secret_value().strip(),
                base_url=s.openai_base_url or None,
                timeout=60,
            )
        kind = content_type.split(";")[0].strip() or "audio/webm"
        ext = {"audio/ogg": "ogg", "audio/mp4": "mp4", "audio/mpeg": "mp3", "audio/wav": "wav"}.get(
            kind, "webm"
        )
        prompt = (
            "A student asking a doubt in Hinglish: Hindi and English mixed, written in Roman letters."
            if language.strip().lower() == "hinglish"
            else "A student asking a doubt about a lesson."
        )
        try:
            r = await self._transcriber.audio.transcriptions.create(
                model=s.animator_doubt_transcribe_model, file=(f"doubt.{ext}", audio, kind), prompt=prompt
            )
        except Exception as e:
            log.warning("transcription failed: %s", e)
            raise DoubtError(502, "Could not understand the recording. Please type the doubt.") from e
        return str(getattr(r, "text", "") or "").strip()
