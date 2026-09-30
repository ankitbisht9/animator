"""The speech script: what the voice reads for each caption (ADR-041).

A voice reads Roman-script Hindi with English spelling rules: "sunati" came out as "sunti", "ghumati"
as "ghoomti". The same words in Devanagari are pronounced right by every voice (edge, Azure, Google,
ElevenLabs). Written punctuation is also not spoken punctuation: ":" and ";" became full stops.

So, for Hinglish lessons, a small model call turns each caption into its spoken form: Hindi words in
Devanagari, English terms as they are, punctuation as a teacher would pause, and the tone of the
sentence (curious, encouraging, ...). It returns exactly one spoken entry per caption word, so the
captions keep lighting up word by word. The captions themselves never change.

It is part of the voice layer only: it runs when narration audio is on, is cached by caption text,
and on any failure (or a reply that does not line up with the caption words) the plain caption
text is spoken, as before.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from animator.config import Settings
from animator.llm import CallContext, LLMClient, LLMRequest, Message
from animator.tts.text import caption_words

log = logging.getLogger("animator.tts")

VERSION = "speech-script.v1"
TONES = ("neutral", "curious", "warm", "encouraging", "excited", "thoughtful", "serious")

INSTRUCTIONS = (Path(__file__).parent / "speech_script.v1.md").read_text(encoding="utf-8")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "captions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "tone": {"type": "string", "enum": list(TONES)},
                    "spoken": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["n", "tone", "spoken"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["captions"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Script:
    """How one caption is spoken: one entry per caption word, and its tone."""

    spoken: tuple[str, ...]
    tone: str = "neutral"

    @property
    def text(self) -> str:
        return " ".join(s for s in self.spoken if s.strip())


def wants_script(mode: str, language: str) -> bool:
    """`mode` is ANIMATOR_TTS_SCRIPT: off, hinglish (default) or all."""
    return mode == "all" or (mode == "hinglish" and language.strip().lower() == "hinglish")


class SpeechScripter:
    def __init__(self, llm: LLMClient, settings: Settings, directory: Path) -> None:
        self.llm = llm
        self.settings = settings
        self.directory = directory
        self._inflight: dict[str, asyncio.Future[Script | None]] = {}

    def _id(self, text: str, language: str) -> str:
        return hashlib.sha256(f"{VERSION}\n{language.lower()}\n{text}".encode()).hexdigest()[:32]

    def _load(self, sid: str) -> Script | None:
        try:
            data = json.loads((self.directory / f"{sid}.json").read_text(encoding="utf-8"))
            return Script(tuple(data["spoken"]), data.get("tone", "neutral"))
        except (OSError, ValueError, KeyError):
            return None

    def _save(self, sid: str, text: str, script: Script) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        body = {"text": text, "spoken": list(script.spoken), "tone": script.tone, "version": VERSION}
        tmp = self.directory / f"{sid}.json.tmp"
        tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.directory / f"{sid}.json")

    async def scripts(
        self, texts: Sequence[str], language: str, ctx: CallContext | None, generation_id: str | None = None
    ) -> dict[str, Script]:
        """Spoken forms for the captions that could be scripted (cached, or one call for the rest).
        Captions missing from the result are spoken as written."""
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
                made = await self._ask(todo, language, ctx, generation_id)
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
        self, texts: list[str], language: str, ctx: CallContext | None, generation_id: str | None
    ) -> dict[str, Script]:
        lines = []
        for n, text in enumerate(texts, 1):
            words = caption_words(text)
            numbered = " ".join(f"[{k}] {w}" for k, w in enumerate(words, 1))
            lines.append(f"Caption {n} ({len(words)} words): {numbered}")
        models, effort = self.settings.stage_model("lightweight")
        req = LLMRequest(
            stage="lightweight",
            prompt_version=VERSION,
            instructions=INSTRUCTIONS,
            messages=[Message.user(f"Lesson language: {language}.\n\n" + "\n".join(lines))],
            schema_name="SpeechScript",
            schema=SCHEMA,
            models=models,
            effort=effort,
            max_output_tokens=4000 + 60 * sum(len(caption_words(t)) for t in texts),
            generation_id=generation_id,
            label="speech script",
            cache_key=f"animator-{VERSION}",
        )
        result = await self.llm.generate(req, ctx or CallContext())
        made: dict[str, Script] = {}
        for item in (result.data or {}).get("captions") or []:
            n = int(item.get("n", 0))
            if not 1 <= n <= len(texts):
                continue
            text = texts[n - 1]
            spoken = [str(s).strip() for s in item.get("spoken") or []]
            if len(spoken) != len(caption_words(text)) or not any(spoken):
                log.info("speech script for %r does not line up with its words: spoken as written", text[:60])
                continue
            tone = item.get("tone") if item.get("tone") in TONES else "neutral"
            made[text] = Script(tuple(spoken), str(tone))
        return made
