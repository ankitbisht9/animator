"""The teacher's voice (ADR-041): no dead air between sentences (beats fitted to when the last word
ends), sentences spoken knowing their neighbours, the speech script (Hindi in Devanagari so the voice
pronounces it right, spoken punctuation, a tone) with the captions still lit word by word, and the
ElevenLabs delivery settings."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from pathlib import Path
from typing import Any

import httpx2

from animator.engine_client import EngineClient, EngineWorkerPool
from animator.jobs.runner import GenerationRunner
from animator.jobs.store import MemoryStore
from animator.llm import FakeLLM, LLMRequest, Usage
from animator.pipeline.generator import Pipeline
from animator.pipeline.models import Generation, GenerationRequest
from animator.pipeline.narration import floors, needs, retime
from animator.tts import ElevenLabsTTS, FakeTTS, SpeechCache, Spoken, WordMark, caption_words, speech_text
from animator.tts.script import Script, SpeechScripter, wants_script
from animator.tts.types import SpeechContext
from animator_contracts.protocol import Beat
from tests.test_pipeline import SCENES, settings
from tests.test_pipeline import Script as LessonScript
from tests.test_tts import mock, mp3_frames

# ---------------------------------------------------------------------- spoken punctuation


def test_written_punctuation_is_spoken_as_a_pause() -> None:
    assert speech_text("Ab tumhari baari: legs 5 aur 12 hain; hypotenuse x kya hoga?") == (
        "Ab tumhari baari, legs 5 aur 12 hain, hypotenuse x kya hoga?"
    )
    assert speech_text("Class starts at 10:30 ; sharp") == "Class starts at 10:30 , sharp"


# ---------------------------------------------------------------------- the speech script


def script_reply(req: LLMRequest) -> dict[str, Any]:
    """A model that writes every caption word in capitals (a stand-in for Devanagari)."""
    text = req.messages[0].parts[0].text  # type: ignore[union-attr]
    out = []
    for n, line in enumerate(re.findall(r"Caption \d+ \(\d+ words\): (.*)", text), 1):
        words = re.findall(r"\[\d+\] (\S+)", line)
        out.append(
            {
                "n": n,
                "tone": "curious" if line.endswith("?") else "neutral",
                "spoken": [w.upper() for w in words],
            }
        )
    return {"captions": out}


async def test_the_script_lines_up_with_the_caption_words_and_is_cached(tmp_path: Path) -> None:
    calls: list[LLMRequest] = []

    def handler(req: LLMRequest) -> Any:
        calls.append(req)
        return script_reply(req)

    scripter = SpeechScripter(FakeLLM(handler), settings(animator_model_lightweight="m-light"), tmp_path)
    texts = ["Pehle **predict** karo: kya hoga?", "Squares jodo."]
    got = await scripter.scripts(texts, "Hinglish", None)
    assert got[texts[0]] == Script(("PEHLE", "PREDICT", "KARO:", "KYA", "HOGA?"), "curious")
    assert got[texts[1]].text == "SQUARES JODO." and got[texts[1]].tone == "neutral"
    assert calls[0].stage == "lightweight" and calls[0].label == "speech script"
    again = await SpeechScripter(
        FakeLLM(handler), settings(animator_model_lightweight="m-light"), tmp_path
    ).scripts(texts, "Hinglish", None)
    assert again == got and len(calls) == 1  # from disk
    assert wants_script("hinglish", "Hinglish") and not wants_script("hinglish", "English")
    assert wants_script("all", "English") and not wants_script("off", "Hinglish")


async def test_a_script_that_does_not_line_up_or_a_failed_call_speaks_the_caption(tmp_path: Path) -> None:
    def short(req: LLMRequest) -> Any:
        return {"captions": [{"n": 1, "tone": "neutral", "spoken": ["ONLY"]}]}

    assert (
        await SpeechScripter(FakeLLM(short), settings(), tmp_path).scripts(["two words"], "Hinglish", None)
        == {}
    )

    def broken(req: LLMRequest) -> Any:
        raise RuntimeError("model down")

    assert (
        await SpeechScripter(FakeLLM(broken), settings(), tmp_path / "b").scripts(["hello"], "Hinglish", None)
        == {}
    )


async def test_the_voice_reads_the_script_and_the_caption_keeps_its_word_times(tmp_path: Path) -> None:
    tts = FakeTTS(seconds_per_word=0.5)
    cache = SpeechCache(tts, tmp_path)
    caption = "Yeh kahani **sunati** hai"
    script = Script(("यह", "कहानी", "सुनाती", "है"), "warm")
    context = SpeechContext(previous="पहला वाक्य", following="अगला वाक्य")
    spoken = await cache.speak(caption, script=script, context=context)
    assert tts.calls == ["यह कहानी सुनाती है"]
    assert tts.contexts[0] == SpeechContext(previous="पहला वाक्य", following="अगला वाक्य", tone="warm")
    assert spoken.marks is not None and [m.text for m in spoken.marks] == caption_words(caption)
    assert spoken.marks[2] == WordMark("sunati", 1.1, 1.5)
    plain = await cache.speak(caption)  # the same caption without a script: its own audio
    assert plain.file != spoken.file and tts.calls[-1] == "Yeh kahani sunati hai"


# ---------------------------------------------------------------------- ElevenLabs delivery


async def test_elevenlabs_sends_the_neighbours_and_the_delivery_settings() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        body = json.loads(req.content)
        bodies.append(body)
        chars = list(body["text"])
        times = [0.05 * i for i in range(len(chars) + 1)]
        return httpx2.Response(
            200,
            json={
                "audio_base64": base64.b64encode(mp3_frames(20)).decode(),
                "alignment": {
                    "characters": chars,
                    "character_start_times_seconds": times[:-1],
                    "character_end_times_seconds": times[1:],
                },
            },
        )

    ctx = SpeechContext(previous="Pehle yeh.", following="Phir woh.", tone="curious")
    flash = ElevenLabsTTS("k", "v", "eleven_flash_v2_5", transport=mock(handler), stability=0.35, style=0.4)
    await flash.synthesize("Kya hoga?", context=ctx)
    assert bodies[0]["text"] == "Kya hoga?"  # no audio tags outside v3
    assert bodies[0]["previous_text"] == "Pehle yeh." and bodies[0]["next_text"] == "Phir woh."
    assert bodies[0]["voice_settings"]["stability"] == 0.35 and bodies[0]["voice_settings"]["style"] == 0.4
    v3 = ElevenLabsTTS("k", "v", "eleven_v3", transport=mock(handler), stability=0.35)
    speech = await v3.synthesize("Kya hoga?", context=ctx)
    assert bodies[1]["text"] == "[curious] Kya hoga?" and bodies[1]["voice_settings"] == {"stability": 0.5}
    assert "previous_text" not in bodies[1]
    assert speech.marks is not None and [m.text for m in speech.marks] == [
        "Kya",
        "hoga?",
    ]  # the tag is not a word


# ---------------------------------------------------------------------- no dead air between sentences


def spoken(duration: float, last_word_end: float) -> Spoken:
    return Spoken("x.mp3", duration, (WordMark("one", 0.1, 0.5), WordMark("two", 0.6, last_word_end)))


def test_a_sentence_needs_time_until_its_last_word_not_the_silence_after_it() -> None:
    beats = [Beat(id="b1", text="one two", start=0, end=6), Beat(id="b2", text="one two", start=6, end=12)]
    need = needs({"b1": spoken(3.0, 2.1), "b2": spoken(3.0, 2.1)}, beats, 0.35)
    assert need["b1"] == 2.1 + 0.35  # edge adds about 0.9 s of silence after the last word: not counted
    assert need["b2"] == 2.1 + 0.6  # the scene's last sentence gets a longer rest


def test_fitting_shortens_long_silences_but_keeps_what_each_beat_starts() -> None:
    dsl = {
        "duration": 12,
        "narration": {"beats": [{"id": "b1", "text": "x"}, {"id": "b2", "text": "y", "at": 6}]},
        "timeline": [
            {"id": "a1", "start": {"at": "beat:b1", "delay": 3.0}},
            {"id": "a2", "start": {"at": "beat:b2", "delay": 0.4}},
        ],
    }
    beats = [Beat(id="b1", text="x", start=0, end=6), Beat(id="b2", text="y", start=6, end=12)]
    assert floors(dsl, beats) == {"b1": 3.3, "b2": 0.7}
    need = {"b1": 2.5, "b2": 2.7}
    fitted = retime(dsl, beats, need, floor=floors(dsl, beats))
    assert fitted is not None
    assert [b["at"] for b in fitted["narration"]["beats"]] == [0, 3.3]  # b1 keeps room for its 3 s action
    assert fitted["duration"] == 6.1
    halfway = retime(dsl, beats, need, floor=floors(dsl, beats), keep=0.5)  # the next try
    assert halfway is not None and [b["at"] for b in halfway["narration"]["beats"]] == [0, 4.25]
    assert halfway["duration"] == 8.7
    assert retime(dsl, beats, need) is None  # without fitting, beats only grow: nothing to change


# ---------------------------------------------------------------------- a Hinglish lesson end to end


async def test_a_hinglish_lesson_is_scripted_fitted_and_still_valid(
    pool: EngineWorkerPool, tmp_path: Path
) -> None:
    lesson = LessonScript()
    labels: list[str] = []

    def handler(req: LLMRequest) -> Any:
        labels.append(req.label)
        return script_reply(req) if req.label == "speech script" else lesson(req)

    tts = FakeTTS(seconds_per_word=0.25)
    s = settings(
        animator_tts_enabled=True, animator_tts_cache_dir=tmp_path, animator_model_lightweight="m-light"
    )
    speech = SpeechCache(tts, tmp_path)
    fake = FakeLLM(handler, usage=Usage(1000, 0, 500), cost=0.01)
    runner = GenerationRunner(MemoryStore(), Pipeline(EngineClient(pool), fake, s, None, speech), s)
    gen = await runner.start(GenerationRequest(topic="Archimedes' principle", language="Hinglish"))
    await asyncio.wait_for(runner._tasks[gen.id], timeout=120)
    got: Generation | None = await runner.get(gen.id)
    assert got is not None and got.status == "complete"
    assert all(t == t.upper() for t in tts.calls)  # the voice read the scripts
    assert any(c is not None and c.following for c in tts.contexts)  # and knew the next sentence
    for rec, original in zip(got.scenes, SCENES, strict=False):
        assert rec.status == "ready" and rec.narration and rec.dsl is not None
        texts = [b["text"] for b in rec.dsl["narration"]["beats"]]
        for b, text in zip(rec.narration["beats"], texts, strict=True):
            assert len(b["words"]) == len(caption_words(text))  # captions still light up word by word
        assert rec.duration is not None and rec.duration < original["duration"]  # short speech: no dead air
    assert "speech script" in labels  # a lesson call like any other: its cost is counted
    assert got.metrics.llm_calls == len(labels)
