"""The speech script: what the voice reads for each caption (ADR-041), and how (ADR-045).

A voice reads Roman-script Hindi with English spelling rules: "sunati" came out as "sunti", "ghumati"
as "ghoomti". The same words in Devanagari are pronounced right by every voice (edge, Azure, Google,
ElevenLabs). Written punctuation is also not spoken punctuation: ":" and ";" became full stops.

So, for Hinglish lessons, a small model call turns each caption into its spoken form: Hindi words in
Devanagari, English terms as they are, punctuation as a teacher would pause, and the tone of the
sentence (curious, encouraging, ...). It returns exactly one spoken entry per caption word, so the
captions keep lighting up word by word. The captions themselves never change.

With voice modulation on (ADR-045, `ANIMATOR_TTS_PROSODY`), the same call is also the voice director,
for every lesson language: per caption its pace (slow for a definition or an equation), the words to
stress and where a teacher pauses before a key point, knowing what the picture shows while it is said.
Lessons that are not Hinglish keep their words exactly as written; only the delivery is used.
`animator.tts.prosody` turns the delivery into what the voice model acts on and into the timing.

It is part of the voice layer only: it runs when narration audio is on, is cached by caption text,
and on any failure (or a reply that does not line up with the caption words) the plain caption
text is spoken, as before.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from animator.config import Settings
from animator.llm import CallContext, LLMClient, LLMRequest, Message
from animator.tts.prosody import Delivery
from animator.tts.text import caption_words, speech_text

log = logging.getLogger("animator.tts")

VERSION = "speech-script.v1"
DIRECTED = "speech-script.v2"  # with the delivery (ADR-045)
TONES = ("neutral", "curious", "warm", "encouraging", "excited", "thoughtful", "serious")
PACES = ("normal", "slow")

INSTRUCTIONS = (Path(__file__).parent / "speech_script.v1.md").read_text(encoding="utf-8")
DIRECTED_INSTRUCTIONS = (Path(__file__).parent / "speech_script.v2.md").read_text(encoding="utf-8")


def _schema(directed: bool) -> dict[str, Any]:
    item: dict[str, Any] = {
        "n": {"type": "integer"},
        "tone": {"type": "string", "enum": list(TONES)},
        "spoken": {"type": "array", "items": {"type": "string"}},
    }
    if directed:
        item["pace"] = {"type": "string", "enum": list(PACES)}
        item["stress"] = {"type": "array", "items": {"type": "integer"}}
        item["pause_before"] = {"type": "array", "items": {"type": "integer"}}
    return {
        "type": "object",
        "properties": {
            "captions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": item,
                    "required": list(item),
                    "additionalProperties": False,
                },
            }
        },
        "required": ["captions"],
        "additionalProperties": False,
    }


SCHEMA = _schema(False)
DIRECTED_SCHEMA = _schema(True)


@dataclass(frozen=True)
class Script:
    """How one caption is spoken: one entry per caption word, and its tone. With voice modulation,
    also the caption words (0-based) to stress, those a pause goes before, and where the tone and
    pace change (`deliveries`; empty: one delivery, `tone` at normal pace)."""

    spoken: tuple[str, ...]
    tone: str = "neutral"
    stress: frozenset[int] = field(default_factory=frozenset)
    pause_before: frozenset[int] = field(default_factory=frozenset)
    deliveries: tuple[Delivery, ...] = ()

    @property
    def text(self) -> str:
        return " ".join(s for s in self.spoken if s.strip())

    @property
    def directed(self) -> bool:
        return bool(self.deliveries)

    @property
    def delivery_key(self) -> str:
        """Everything about the delivery that changes the audio (part of the speech cache key)."""
        if not self.directed:
            return ""
        parts = ",".join(f"{d.start}:{d.tone}:{d.pace}" for d in self.deliveries)
        return f"s{sorted(self.stress)}|p{sorted(self.pause_before)}|d{parts}"


def wants_script(mode: str, language: str) -> bool:
    """`mode` is ANIMATOR_TTS_SCRIPT: off, hinglish (default) or all."""
    return mode == "all" or (mode == "hinglish" and language.strip().lower() == "hinglish")


class SpeechScripter:
    """`directed`: the call also plans the delivery (voice modulation, ADR-045)."""

    def __init__(self, llm: LLMClient, settings: Settings, directory: Path, directed: bool = False) -> None:
        self.llm = llm
        self.settings = settings
        self.directory = directory
        self.directed = directed
        self.version = DIRECTED if directed else VERSION
        self._inflight: dict[str, asyncio.Future[Script | None]] = {}

    def _id(self, text: str, language: str) -> str:
        return hashlib.sha256(f"{self.version}\n{language.lower()}\n{text}".encode()).hexdigest()[:32]

    def _load(self, sid: str) -> Script | None:
        try:
            data = json.loads((self.directory / f"{sid}.json").read_text(encoding="utf-8"))
            return Script(
                tuple(data["spoken"]),
                data.get("tone", "neutral"),
                frozenset(data.get("stress") or ()),
                frozenset(data.get("pause_before") or ()),
                tuple(Delivery(int(d[0]), str(d[1]), str(d[2])) for d in data.get("deliveries") or ()),
            )
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            return None

    def _save(self, sid: str, text: str, script: Script) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        body: dict[str, Any] = {"text": text, "spoken": list(script.spoken), "tone": script.tone}
        if script.directed:
            body["stress"] = sorted(script.stress)
            body["pause_before"] = sorted(script.pause_before)
            body["deliveries"] = [[d.start, d.tone, d.pace] for d in script.deliveries]
        body["version"] = self.version
        tmp = self.directory / f"{sid}.json.tmp"
        tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.directory / f"{sid}.json")

    async def scripts(
        self,
        texts: Sequence[str],
        language: str,
        ctx: CallContext | None,
        generation_id: str | None = None,
        cues: Mapping[str, str] | None = None,
    ) -> dict[str, Script]:
        """Spoken forms for the captions that could be scripted (cached, or one call for the rest).
        Captions missing from the result are spoken as written. `cues`: caption -> what the picture
        shows while it is said (the voice stresses what is revealed)."""
        out: dict[str, Script] = {}
        todo: list[str] = []
        waits: dict[str, asyncio.Future[Script | None]] = {}
        for text in dict.fromkeys(t for t in texts if caption_words(t)):
            sid = self._id(text, language)
            hit = self._load(sid)
            if hit is not None:
                out[text] = hit
            elif sid in self._inflight:
                waits[text] = self._inflight[sid]
            else:
                todo.append(text)
        if todo:
            loop = asyncio.get_running_loop()
            futures = {text: loop.create_future() for text in todo}
            for text, fut in futures.items():
                self._inflight[self._id(text, language)] = fut
            try:
                made = await self._ask(todo, language, ctx, generation_id, cues or {})
            except Exception as e:  # never let the script break the voice
                log.warning("speech script failed (%s): speaking the captions as written", e)
                made = {}
            for text, fut in futures.items():
                script = made.get(text)
                if script is not None:
                    self._save(self._id(text, language), text, script)
                    out[text] = script
                if not fut.done():
                    fut.set_result(script)
                self._inflight.pop(self._id(text, language), None)
        for text, fut in waits.items():
            script = await asyncio.shield(fut)
            if script is not None:
                out[text] = script
        return out

    async def _ask(
        self,
        texts: list[str],
        language: str,
        ctx: CallContext | None,
        generation_id: str | None,
        cues: Mapping[str, str],
    ) -> dict[str, Script]:
        rewrite = wants_script(self.settings.animator_tts_script, language)
        lines = []
        for n, text in enumerate(texts, 1):
            words = caption_words(text)
            numbered = " ".join(f"[{k}] {w}" for k, w in enumerate(words, 1))
            if self.directed and cues.get(text):
                lines.append(f"Caption {n} ({len(words)} words; on screen: {cues[text]}): {numbered}")
            else:
                lines.append(f"Caption {n} ({len(words)} words): {numbered}")
        models, effort = self.settings.stage_model("lightweight")
        head = f"Lesson language: {language}."
        if self.directed and not rewrite:
            head += " Keep every word exactly as written in `spoken` (only the delivery is needed)."
        req = LLMRequest(
            stage="lightweight",
            prompt_version=self.version,
            instructions=DIRECTED_INSTRUCTIONS if self.directed else INSTRUCTIONS,
            messages=[Message.user(head + "\n\n" + "\n".join(lines))],
            schema_name="SpeechScript",
            schema=DIRECTED_SCHEMA if self.directed else SCHEMA,
            models=models,
            effort=effort,
            max_output_tokens=4000 + 60 * sum(len(caption_words(t)) for t in texts),
            generation_id=generation_id,
            label="speech script",
            cache_key=f"animator-{self.version}",
        )
        result = await self.llm.generate(req, ctx or CallContext())
        made: dict[str, Script] = {}
        for item in (result.data or {}).get("captions") or []:
            n = int(item.get("n", 0))
            if not 1 <= n <= len(texts):
                continue
            text = texts[n - 1]
            words = caption_words(text)
            spoken = [str(s).strip() for s in item.get("spoken") or []]
            if self.directed and not rewrite:
                spoken = speech_text(text).split(" ")  # the words stay exactly as written
            if len(spoken) != len(words) or not any(spoken):
                log.info("speech script for %r does not line up with its words: spoken as written", text[:60])
                continue
            tone = item.get("tone") if item.get("tone") in TONES else "neutral"
            if not self.directed:
                made[text] = Script(tuple(spoken), str(tone))
                continue
            pace = item.get("pace") if item.get("pace") in PACES else "normal"
            stress = _indices(item.get("stress"), len(words), 2)
            pauses = _indices(item.get("pause_before"), len(words), 1, first=False)
            made[text] = Script(
                tuple(spoken), str(tone), stress, pauses, (Delivery(0, str(tone), str(pace)),)
            )
        return made


def _indices(values: Any, count: int, most: int, first: bool = True) -> frozenset[int]:
    """1-based word numbers from the model -> 0-based indices inside the caption, at most `most`
    (a pause before the first word is no pause: `first` False drops it)."""
    out: list[int] = []
    for v in values or []:
        if isinstance(v, int) and 1 <= v <= count and (first or v > 1) and v - 1 not in out:
            out.append(v - 1)
    return frozenset(out[:most])
