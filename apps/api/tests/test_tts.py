"""Narration audio (ADR-037): caption words and their timings, the providers (over a mocked HTTP
layer, never the network), the speech cache, retiming scenes to their speech (validated by the real
engine), the pipeline with the switch on and off, the API, and the export's narration track."""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import struct
import wave
from pathlib import Path
from typing import Any

import edge_tts
import httpx2
import pytest
from fastapi.testclient import TestClient

from animator.api.app import create_app
from animator.engine_client import EngineClient, EngineWorkerPool
from animator.export.exporter import ExportError
from animator.export.narration import RATE, NarrationAudio
from animator.jobs.runner import GenerationRunner
from animator.jobs.store import MemoryStore
from animator.llm import FakeLLM, Usage
from animator.pipeline.generator import Pipeline
from animator.pipeline.language import language_line
from animator.pipeline.models import Generation, GenerationRequest
from animator.pipeline.narration import narration_record, retime
from animator.tts import (
    AzureTTS,
    EdgeTTS,
    ElevenLabsTTS,
    FakeTTS,
    GoogleTTS,
    KokoroTTS,
    OpenAICompatibleTTS,
    SpeechCache,
    TTSError,
    WordMark,
    build_speech,
    caption_words,
    env_duplicates,
    speech_text,
    strip_markup,
    tts_problem,
    word_times,
)
from animator.tts.audio import mp3_duration, silent_wav, wav_duration
from animator_contracts.protocol import Beat
from tests.test_generations_api import read_events
from tests.test_pipeline import SCENES, Script, settings

SECRET = "tts-secret-key-that-must-never-leak"

# ---------------------------------------------------------------------- caption words


def test_caption_words_follow_the_engine_markup_rules() -> None:
    assert strip_markup("Look at the **tank**.") == "Look at the tank."
    assert strip_markup("F_{b} = ρ g V_{sub}") == "Fb = ρ g Vsub"  # noqa: RUF001
    assert strip_markup("an odd ** marker") == "an odd ** marker"  # unbalanced: kept, like the engine
    assert caption_words("Look at\nthe  **tank**.") == ["Look", "at", "the", "tank."]
    assert speech_text("Look at\nthe  **tank**.") == "Look at the tank."
    assert speech_text("  ") == ""


def check(times: list[tuple[float, float]], duration: float) -> None:
    starts = [s for s, _ in times]
    assert starts == sorted(starts)
    assert all(0 <= s <= e <= duration for s, e in times)


def test_word_times_use_the_providers_marks() -> None:
    words = caption_words("What turns when you push **down**?")
    marks = [
        WordMark("What", 0.1, 0.4),
        WordMark("turns", 0.41, 0.77),
        WordMark("when", 0.79, 0.91),
        WordMark("you", 0.93, 1.04),
        WordMark("push", 1.05, 1.36),
        WordMark("down", 1.4, 1.8),
    ]
    times = word_times(words, marks, 2.2)
    assert times == [(0.1, 0.4), (0.41, 0.77), (0.79, 0.91), (0.93, 1.04), (1.05, 1.36), (1.4, 1.8)]


def test_a_mark_that_covers_several_words_is_shared_between_them() -> None:
    words = caption_words("It falls at 9.8 m/s² every second")
    marks = [
        WordMark("It", 0.1, 0.2),
        WordMark("falls", 0.2, 0.5),
        WordMark("at", 0.5, 0.6),
        WordMark("9.8 m/s²", 0.6, 2.6),  # said as "nine point eight meters per second squared"
        WordMark("every", 2.7, 3.0),
        WordMark("second", 3.0, 3.4),
    ]
    times = word_times(words, marks, 3.6)
    check(times, 3.6)
    nine, ms = times[3], times[4]
    assert nine[0] == 0.6 and ms[1] == 2.6 and nine[1] == ms[0]  # split, back to back
    assert times[5] == (2.7, 3.0)


def test_words_said_differently_are_estimated_between_their_neighbours() -> None:
    words = caption_words("It weighs 12 kg today")
    marks = [WordMark("It", 0.1, 0.2), WordMark("weighs", 0.2, 0.5), WordMark("today", 2.0, 2.4)]
    times = word_times(words, marks, 2.6)
    check(times, 2.6)
    assert 0.5 <= times[2][0] < times[3][0] < 2.0 and times[3][1] <= 2.0


def test_without_marks_times_follow_length_and_punctuation() -> None:
    words = caption_words("Push it. Then stop, and look")
    times = word_times(words, None, 4.0)
    check(times, 4.0)
    assert times[0][0] > 0 and times[-1][1] < 4.0  # room for the silence around speech
    gap_after_period = times[2][0] - times[1][1]
    gap_between_words = times[1][0] - times[0][1]
    assert gap_after_period > gap_between_words
    assert word_times([], None, 1.0) == []


# ---------------------------------------------------------------------- audio


def mp3_frames(n: int, xing: bool = False, id3: bool = False) -> bytes:
    """MPEG-2 Layer III, 48 kbps, 24 kHz mono: 144-byte frames of 576 samples (what edge-tts sends)."""
    header = bytes([0xFF, 0xF3, 0x54, 0xC4])  # version 2, layer III, 48 kbps, 24 kHz, no padding
    frame = header + b"\x00" * 140
    data = frame * n
    if xing:
        data = header + b"\x00" * 4 + b"Xing" + b"\x00" * 132 + data
    if id3:
        data = b"ID3\x04\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10 + data
    return data


def test_mp3_duration_counts_frames() -> None:
    assert mp3_duration(mp3_frames(100)) == pytest.approx(100 * 576 / 24000)
    assert mp3_duration(mp3_frames(100, xing=True, id3=True)) == pytest.approx(100 * 576 / 24000)
    assert mp3_duration(b"not audio") == 0.0
    assert wav_duration(silent_wav(1.25)) == pytest.approx(1.25)


# ---------------------------------------------------------------------- providers (no network)


def mock(handler: Any) -> httpx2.MockTransport:
    return httpx2.MockTransport(handler)


async def test_google_reads_word_starts_from_ssml_marks() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen["key"] = req.headers.get("x-goog-api-key")
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        audio = base64.b64encode(mp3_frames(50)).decode()
        points = [{"markName": "w0", "timeSeconds": 0.05}, {"markName": "w1", "timeSeconds": 0.4}]
        return httpx2.Response(200, json={"audioContent": audio, "timepoints": points})

    tts = GoogleTTS(SECRET, "en-GB-Neural2-A", 1.1, transport=mock(handler))
    speech = await tts.synthesize("Hello <world>")
    assert seen["key"] == SECRET and SECRET not in seen["url"]
    body = seen["body"]
    assert body["input"]["ssml"] == '<speak><mark name="w0"/>Hello <mark name="w1"/>&lt;world&gt;</speak>'
    assert body["voice"] == {"languageCode": "en-GB", "name": "en-GB-Neural2-A"}
    assert body["audioConfig"]["speakingRate"] == 1.1 and body["enableTimePointing"] == ["SSML_MARK"]
    assert speech.format == "mp3" and speech.duration_s == pytest.approx(1.2)
    assert speech.marks == (WordMark("Hello", 0.05, 0.4), WordMark("<world>", 0.4, 1.2))


async def test_elevenlabs_groups_character_timings_into_words() -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        assert req.headers["xi-api-key"] == SECRET
        assert req.url.path == "/v1/text-to-speech/voice123/with-timestamps"
        body = json.loads(req.content)
        assert body["model_id"] == "eleven_multilingual_v2" and body["voice_settings"]["speed"] == 1.2
        chars = list("Hi you")
        return httpx2.Response(
            200,
            json={
                "audio_base64": base64.b64encode(mp3_frames(25)).decode(),
                "alignment": {
                    "characters": chars,
                    "character_start_times_seconds": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
                    "character_end_times_seconds": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
                },
            },
        )

    tts = ElevenLabsTTS(SECRET, "voice123", speed=1.5, transport=mock(handler))
    speech = await tts.synthesize("Hi you")
    assert speech.marks == (WordMark("Hi", 0.0, 0.2), WordMark("you", 0.3, 0.6))


async def test_azure_and_openai_speak_without_word_timings() -> None:
    def azure(req: httpx2.Request) -> httpx2.Response:
        assert req.url.host == "westeurope.tts.speech.microsoft.com"
        assert req.headers["ocp-apim-subscription-key"] == SECRET
        ssml = req.content.decode()
        assert 'xml:lang="en-US"' in ssml and 'rate="+10%"' in ssml and "a &amp; b" in ssml
        return httpx2.Response(200, content=mp3_frames(10))

    speech = await AzureTTS(SECRET, "westeurope", speed=1.1, transport=mock(azure)).synthesize("a & b")
    assert speech.marks is None and speech.duration_s == pytest.approx(0.24)

    bodies: list[dict[str, Any]] = []

    def openai(req: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(req.content))
        assert req.headers.get("authorization") == (f"Bearer {SECRET}" if "openai" in str(req.url) else None)
        return httpx2.Response(200, content=mp3_frames(10))

    await OpenAICompatibleTTS(SECRET, instructions="warm", transport=mock(openai)).synthesize("Hi")
    await OpenAICompatibleTTS(
        "", "http://localhost:8880/v1", "af_heart", "kokoro", instructions="warm", transport=mock(openai)
    ).synthesize("Hi")
    assert bodies[0]["model"] == "gpt-4o-mini-tts" and bodies[0]["instructions"] == "warm"
    assert bodies[1]["model"] == "kokoro" and "instructions" not in bodies[1]


async def test_provider_errors_never_contain_the_key() -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(403, json={"error": {"message": "API key not valid"}})

    with pytest.raises(TTSError) as e:
        await GoogleTTS(SECRET, transport=mock(handler)).synthesize("Hi")
    assert "HTTP 403" in str(e.value) and "API key not valid" in str(e.value) and SECRET not in str(e.value)
    with pytest.raises(TTSError, match="GOOGLE_TTS_API_KEY"):
        GoogleTTS("")
    with pytest.raises(TTSError, match="ELEVENLABS_API_KEY"):
        ElevenLabsTTS("")
    with pytest.raises(TTSError, match="AZURE_SPEECH"):
        AzureTTS(SECRET, "")
    with pytest.raises(TTSError, match="OPENAI_API_KEY"):
        OpenAICompatibleTTS("")
    OpenAICompatibleTTS("", "http://localhost:8880/v1")  # a local server needs no key


async def test_kokoro_reads_word_times_from_captioned_speech() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append((str(req.url), json.loads(req.content)))
        stamps = [
            {"word": "Socho", "start_time": 0.03, "end_time": 0.5},
            {"word": ",", "start_time": 0.5, "end_time": 0.6},
            {"word": "gravity", "start_time": 0.6, "end_time": 1.1},
        ]
        audio = base64.b64encode(mp3_frames(60)).decode()
        return httpx2.Response(200, json={"audio": audio, "timestamps": stamps})

    tts = KokoroTTS("http://192.168.0.140:8880/v1/", transport=mock(handler))  # the OpenAI-style URL works
    speech = await tts.synthesize("Socho, gravity", "hm_omega")
    url, body = seen[0]
    assert url == "http://192.168.0.140:8880/dev/captioned_speech"
    assert body["voice"] == "hm_omega" and body["return_timestamps"] is True and body["stream"] is False
    assert speech.duration_s == pytest.approx(60 * 576 / 24000)
    assert speech.marks is not None and speech.marks[0] == WordMark("Socho", 0.03, 0.5)
    # Punctuation entries are ignored when the times are matched to the caption's words.
    times = word_times(caption_words("Socho, gravity"), speech.marks, speech.duration_s)
    assert times == [(0.03, 0.5), (0.6, 1.1)]
    await KokoroTTS(transport=mock(handler)).synthesize("Hi")
    assert seen[1][0] == "http://localhost:8880/dev/captioned_speech" and seen[1][1]["voice"] == "af_heart"


async def test_edge_collects_audio_and_word_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    made: dict[str, Any] = {}

    class FakeCommunicate:
        def __init__(self, text: str, voice: str, **kw: Any) -> None:
            made.update(text=text, voice=voice, **kw)

        async def stream(self) -> Any:
            yield {"type": "WordBoundary", "offset": 1_000_000, "duration": 3_000_000, "text": "Hello"}
            yield {"type": "audio", "data": mp3_frames(20)}
            yield {"type": "WordBoundary", "offset": 4_500_000, "duration": 2_000_000, "text": "there"}
            yield {"type": "audio", "data": mp3_frames(5)}

    monkeypatch.setattr(edge_tts, "Communicate", FakeCommunicate)
    speech = await EdgeTTS(speed=0.9).synthesize("Hello there")
    assert made["voice"] == "en-US-AvaMultilingualNeural" and made["rate"] == "-10%"
    assert made["boundary"] == "WordBoundary"
    assert speech.duration_s == pytest.approx(25 * 576 / 24000)
    assert speech.marks == (WordMark("Hello", 0.1, 0.4), WordMark("there", 0.45, 0.65))


# ---------------------------------------------------------------------- the switch


def test_the_switch_and_missing_keys(tmp_path: Path) -> None:
    assert build_speech(settings()) is None  # off by default
    assert tts_problem(settings()) is None
    on = settings(animator_tts_enabled=True, animator_tts_cache_dir=tmp_path)
    speech = build_speech(on)
    assert speech is not None and speech.provider.name == "edge"
    google = settings(animator_tts_enabled=True, animator_tts_provider="google")
    assert build_speech(google) is None  # a missing key turns narration off, never the API
    assert "GOOGLE_TTS_API_KEY" in (tts_problem(google) or "")
    keyed = settings(animator_tts_enabled=True, animator_tts_provider="google", google_tts_api_key=SECRET)
    assert build_speech(keyed) is not None and tts_problem(keyed) is None


# ---------------------------------------------------------------------- cache


async def test_the_cache_synthesizes_each_sentence_once(tmp_path: Path) -> None:
    fake = FakeTTS(delay_s=0.05)
    cache = SpeechCache(fake, tmp_path)
    a, b = await asyncio.gather(cache.speak("Look at the **tank**."), cache.speak("Look at the tank."))
    assert a == b and fake.calls == ["Look at the tank."]  # markup is not spoken
    assert (tmp_path / a.file).is_file() and cache.audio_path(a.file) == tmp_path / a.file
    again = await SpeechCache(FakeTTS(), tmp_path).speak("Look at the tank.")  # from disk
    assert again == a
    assert cache.audio_path("../secret.mp3") is None and cache.audio_path("0" * 32 + ".mp3") is None
    other = SpeechCache(FakeTTS(seconds_per_word=0.5), tmp_path)  # other settings: another file
    assert (await other.speak("Look at the tank.")).file != a.file
    hinglish = await cache.speak("Look at the tank.", "fake_hinglish")  # another voice: another file
    assert hinglish.file != a.file and fake.voices[-1] == "fake_hinglish"


def test_each_lesson_language_has_a_voice(tmp_path: Path) -> None:
    cache = SpeechCache(FakeTTS(), tmp_path)
    assert cache.voice_for("English") == "fake" and cache.voice_for("Spanish") == "fake"
    assert cache.voice_for("Hinglish") == cache.voice_for(" hinglish ") == "fake_hinglish"
    chosen = SpeechCache(FakeTTS(), tmp_path, voices={"Hinglish": "hf_beta"})
    assert chosen.voice_for("Hinglish") == "hf_beta"
    assert SpeechCache(FakeTTS(), tmp_path, voices={"Hinglish": ""}).voice_for("Hinglish") == "fake_hinglish"
    on = settings(
        animator_tts_enabled=True, animator_tts_provider="kokoro", animator_tts_voice_hinglish="hf_beta"
    )
    speech = build_speech(on)
    assert speech is not None and speech.provider.voice == "af_heart"
    assert speech.voice_for("Hinglish") == "hf_beta"
    default = build_speech(settings(animator_tts_enabled=True, animator_tts_provider="kokoro"))
    assert default is not None and default.voice_for("Hinglish") == "hm_omega"
    edge = build_speech(settings(animator_tts_enabled=True))
    assert edge is not None and edge.voice_for("Hinglish") == "en-IN-NeerjaExpressiveNeural"


def test_a_setting_written_twice_in_env_is_reported(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "ANIMATOR_TTS_PROVIDER=openai\nANIMATOR_TTS_BASE_URL=http://localhost:8880/v1\n"
        "OTHER=1\nOTHER=2\n# ANIMATOR_TTS_VOICE=x\nANIMATOR_TTS_BASE_URL=\n",
        encoding="utf-8",
    )
    assert env_duplicates(env) == ["ANIMATOR_TTS_BASE_URL"]  # only narration settings, not comments
    assert env_duplicates(None) == [] and env_duplicates(tmp_path / "missing") == []
    # The mistake that made lessons silent: Kokoro's model sent to OpenAI.
    wrong = settings(
        animator_tts_enabled=True,
        animator_tts_provider="openai",
        animator_tts_model="kokoro",
        openai_api_key="k",
    )
    assert "ANIMATOR_TTS_BASE_URL" in (tts_problem(wrong) or "")


async def test_prefetch_starts_early_and_failures_surface_when_speech_is_needed(tmp_path: Path) -> None:
    fake = FakeTTS(delay_s=0.05)
    cache = SpeechCache(fake, tmp_path)
    cache.prefetch(["One two.", "", "Three."])
    await asyncio.sleep(0.2)
    assert sorted(fake.calls) == ["One two.", "Three."]
    await cache.speak("One two.")
    assert len(fake.calls) == 2
    failing = SpeechCache(FakeTTS(fail=True), tmp_path / "f", retries=1)
    failing.prefetch(["Nope."])  # a failed prefetch is silent
    with pytest.raises(TTSError):
        await failing.speak("Nope.")
    with pytest.raises(TTSError):
        await failing.speak("**")


# ---------------------------------------------------------------------- retiming


def beats_of(res: Any) -> list[Beat]:
    return list(res.beats)


def test_retime_stretches_only_the_beats_that_need_it() -> None:
    dsl = {
        "duration": 12,
        "narration": {"beats": [{"id": "b1", "text": "x"}, {"id": "b2", "text": "y", "at": 6}]},
        "cues": [{"id": "c", "at": 9}],
        "timeline": [
            {"id": "a1", "start": {"at": 3}},
            {"id": "a2", "start": {"at": "beat:b2", "delay": 0.5}},
            {"id": "a3", "start": {"after": "a2"}},
            {"id": "a4", "start": {"at": 9}},
        ],
        "interactions": [{"id": "q", "at": 11.5}],
    }
    beats = [Beat(id="b1", text="x", start=0, end=6), Beat(id="b2", text="y", start=6, end=12)]
    assert retime(dsl, beats, {"b1": 5.0, "b2": 4.0}) is None  # speech fits already
    out = retime(dsl, beats, {"b1": 9.0, "b2": 4.0})
    assert out is not None and dsl["duration"] == 12  # the input is not changed
    assert [b["at"] for b in out["narration"]["beats"]] == [0, 9.0]
    assert out["timeline"][0]["start"]["at"] == 4.5  # halfway through b1 stays halfway
    assert out["timeline"][1]["start"] == {"at": "beat:b2", "delay": 0.5}  # moves with its beat
    assert out["timeline"][2]["start"] == {"after": "a2"}
    assert out["timeline"][3]["start"]["at"] == 12  # later beats shift by the added time
    assert out["cues"][0]["at"] == 12 and out["interactions"][0]["at"] == 14.5
    assert out["duration"] == 15.1
    assert retime(dsl, beats, {"b1": 200.0}) is None  # longer than a scene may be


async def test_every_fixture_scene_stays_valid_when_stretched_to_slow_speech(pool: EngineWorkerPool) -> None:
    engine = EngineClient(pool)
    prev = None
    for dsl in SCENES:
        res = await engine.compile(dsl, prev)
        assert res.ok
        beats = beats_of(res)
        need = {b.id: (b.end - b.start) * 1.4 + 0.5 for b in beats}  # every beat 40% longer, and more
        out = retime(dsl, beats, need)
        assert out is not None
        again = await engine.compile(out, prev)
        errors = [i.message for i in again.issues if i.severity == "error"]
        assert again.ok, (dsl["id"], errors)
        new = {b.id: b for b in beats_of(again)}
        for b in beats:
            assert new[b.id].end - new[b.id].start >= need[b.id] - 0.01, (dsl["id"], b.id)
        assert again.duration is not None and res.duration is not None and again.duration > res.duration
        actions = (again.compiled or {})["actions"]
        assert all(a["end"] <= again.duration + 1e-6 for a in actions.values())
        prev = again.endState


def test_the_narration_record_times_every_caption_word(tmp_path: Path) -> None:
    cache = SpeechCache(FakeTTS(), tmp_path)
    spoken = {"b1": asyncio.run(cache.speak("Look at the **tank**."))}
    beats = [
        Beat(id="b1", text="Look at the **tank**.", start=0.5, end=3),
        Beat(id="b2", text="-", start=3, end=5),
    ]
    rec = narration_record(cache, beats, spoken)
    assert rec["provider"] == "fake" and len(rec["beats"]) == 1
    beat = rec["beats"][0]
    assert beat["start"] == 0.5 and beat["audio"] == spoken["b1"].file and beat["timed"] is True
    assert len(beat["words"]) == 4 and beat["words"][0] == [0.1, 0.34]


# ---------------------------------------------------------------------- pipeline


async def spoken_lesson(
    pool: EngineWorkerPool, tmp_path: Path, tts: FakeTTS, language: str = "English"
) -> Generation:
    s = settings(animator_tts_enabled=True, animator_tts_cache_dir=tmp_path)
    speech = SpeechCache(tts, tmp_path)
    fake = FakeLLM(Script(), usage=Usage(1000, 0, 500), cost=0.01)
    runner = GenerationRunner(MemoryStore(), Pipeline(EngineClient(pool), fake, s, None, speech), s)
    gen = await runner.start(GenerationRequest(topic="Archimedes' principle", language=language))
    await asyncio.wait_for(runner._tasks[gen.id], timeout=120)
    got = await runner.get(gen.id)
    assert got is not None
    return got


async def test_scenes_get_a_voice_and_stretch_to_fit_it(pool: EngineWorkerPool, tmp_path: Path) -> None:
    gen = await spoken_lesson(pool, tmp_path, FakeTTS(seconds_per_word=0.65))
    assert gen.status == "complete" and [s.status for s in gen.scenes] == ["ready"] * 4
    for rec, original in zip(gen.scenes, SCENES, strict=False):
        assert rec.narration is not None and rec.dsl is not None
        voiced = rec.narration["beats"]
        assert [b["id"] for b in voiced] == [b["id"] for b in rec.dsl["narration"]["beats"]]
        for b, text in zip(voiced, [x["text"] for x in rec.dsl["narration"]["beats"]], strict=True):
            assert len(b["words"]) == len(caption_words(text))
            assert (tmp_path / b["audio"]).is_file()
        starts = [b["start"] for b in voiced] + [rec.duration]
        assert all(starts[i + 1] - starts[i] >= voiced[i]["duration"] for i in range(len(voiced)))
        assert rec.duration is not None and rec.duration > original["duration"]  # slow speech: longer
    assert gen.metrics.tts_chars > 0 and gen.metrics.tts_audio_s > 0 and gen.metrics.tts_failures == 0
    assert all(s.narration and s.narration["voice"] == "fake" for s in gen.scenes)


async def test_a_hinglish_lesson_speaks_with_the_hinglish_voice(
    pool: EngineWorkerPool, tmp_path: Path
) -> None:
    tts = FakeTTS()
    gen = await spoken_lesson(pool, tmp_path, tts, language="Hinglish")
    assert gen.status == "complete" and all(s.narration for s in gen.scenes)
    assert set(tts.voices) == {"fake_hinglish"}  # the prefetch too
    assert all(s.narration and s.narration["voice"] == "fake_hinglish" for s in gen.scenes)


async def test_failing_speech_never_fails_the_lesson(pool: EngineWorkerPool, tmp_path: Path) -> None:
    gen = await spoken_lesson(pool, tmp_path, FakeTTS(fail=True))
    assert gen.status == "complete" and [s.status for s in gen.scenes] == ["ready"] * 4
    assert all(s.narration is None for s in gen.scenes)
    assert [s.duration for s in gen.scenes] == [d["duration"] for d in SCENES[:4]]  # timing untouched
    assert gen.metrics.tts_failures == 4
    assert gen.metrics.tts_error == "fake: failing on purpose"  # shown on the lesson page


# ---------------------------------------------------------------------- API


def app_with(tmp_path: Path, enabled: bool, tts: FakeTTS | None = None) -> TestClient:
    s = settings(
        openai_api_key="sk-proj-secret-value-that-must-never-leak",
        animator_tts_enabled=enabled,
        animator_tts_cache_dir=tmp_path,
    )
    llm = FakeLLM(Script())
    app = create_app(settings=s, llm=llm, store=MemoryStore(), resume_on_start=False, tts=tts)
    return TestClient(app)


def test_the_api_serves_narration_only_when_the_switch_is_on(worker_script: Path, tmp_path: Path) -> None:
    with app_with(tmp_path, True, FakeTTS()) as client:
        cfg = client.get("/api/config").json()["tts"]
        assert cfg == {
            "enabled": True,
            "provider": "edge",
            "voice": "fake",
            "voices": {"Hinglish": "fake_hinglish"},
            "problem": None,
            "warnings": [],  # tests pass their own settings: no .env is read
        }
        gen_id = client.post("/api/generations", json={"topic": "Archimedes' principle"}).json()["id"]
        events = read_events(client, gen_id)
        scenes = [e for e in events if e["type"] == "scene"]
        assert scenes and all(e["narration"] and e["narration"]["beats"] for e in scenes)
        audio = scenes[0]["narration"]["beats"][0]["audio"]
        r = client.get(f"/api/tts/{audio}")
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
        assert "immutable" in r.headers["cache-control"]
        assert client.get("/api/tts/..%2F.env").status_code == 404
        full = client.get(f"/api/generations/{gen_id}").json()
        assert all(s["narration"] for s in full["scenes"])
        stored = client.app.state.runner._live[gen_id]  # type: ignore[attr-defined]

    # The same lesson with the switch off: no narration and no audio leave the API.
    with app_with(tmp_path, False) as client:
        client.app.state.runner._live[gen_id] = stored  # type: ignore[attr-defined]
        assert client.get("/api/config").json()["tts"]["enabled"] is False
        assert client.get(f"/api/tts/{audio}").status_code == 404
        full = client.get(f"/api/generations/{gen_id}").json()
        assert all(s["narration"] is None for s in full["scenes"])
        snapshot = read_events(client, gen_id)[0]
        assert all(s["narration"] is None for s in snapshot["scenes"])


# ---------------------------------------------------------------------- export


def tone(path: Path, seconds: float, level: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack("<h", level) * round(seconds * RATE))


async def test_the_export_track_places_each_beat_at_its_time(tmp_path: Path) -> None:
    a, b, c = "a" * 32 + ".wav", "b" * 32 + ".wav", "c" * 32 + ".wav"
    tone(tmp_path / a, 1.0, 1000)
    tone(tmp_path / b, 0.5, 2000)
    tone(tmp_path / c, 0.5, 3000)
    lesson: dict[str, Any] = {
        "scenes": [{"duration": 4}, {"duration": 3}, {"duration": 2}],
        "narration": [
            {"beats": [{"id": "b1", "start": 0.5, "audio": a}, {"id": "b2", "start": 2.0, "audio": b}]},
            None,
            {"beats": [{"id": "b1", "start": 1.0, "audio": c}]},
        ],
    }
    out = await NarrationAudio(tmp_path).track(lesson, 9.0, tmp_path / "work")
    assert out is not None
    with wave.open(str(out)) as w:
        assert w.getframerate() == RATE and w.getnframes() == 9 * RATE
        pcm = w.readframes(w.getnframes())

    def at(t: float) -> int:
        i = round(t * RATE) * 2
        return int(struct.unpack("<h", pcm[i : i + 2])[0])

    assert [at(0.25), at(1.0), at(1.75), at(2.25), at(3.0), at(8.25), at(8.75)] == [
        0,
        1000,
        0,
        2000,
        0,
        3000,
        0,
    ]
    assert await NarrationAudio(tmp_path).track({"scenes": [{"duration": 1}]}, 1.0, tmp_path) is None
    broken = copy.deepcopy(lesson)
    broken["narration"][0]["beats"][0]["audio"] = "d" * 32 + ".wav"
    with pytest.raises(ExportError, match="ANIMATOR_TTS_ENABLED=0"):
        await NarrationAudio(tmp_path).track(broken, 9.0, tmp_path)


# ---------------------------------------------------------------------- lesson language


def test_english_prompts_are_unchanged_and_hinglish_is_spelled_out() -> None:
    assert language_line("English") == "English"  # English lessons send exactly what they did before
    assert language_line("Spanish") == "Spanish"
    line = language_line("Hinglish")
    assert line == language_line("hinglish") and "Roman" in line and "never in Devanagari" in line
