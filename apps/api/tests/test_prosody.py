"""Voice modulation (ADR-045): the speech script directs how each sentence is said (tone, pace,
stressed words, a pause before a key point), the voice model gets what it can act on (audio tags and
capitals for Eleven v3/v4), a teacher's timing is applied to the audio (pauses at every full stop and
comma, slow sentences slower) with the caption times moved along, and a beat's highlights land on the
moment the thing they highlight is named."""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx2
import pytest

from animator.llm import FakeLLM, LLMRequest
from animator.pipeline.narration import sync_highlights, take_script
from animator.tts import ElevenLabsTTS, FakeTTS, SpeechCache, caption_words
from animator.tts.audio import silent_wav
from animator.tts.prosody import Delivery, Shaping, plan_pauses, render, shape, tags_supported
from animator.tts.script import Script, SpeechScripter
from animator.tts.types import Speech, WordMark
from tests.test_pipeline import settings
from tests.test_tts import mock, mp3_frames

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def audio_seconds(path: Path) -> float:
    """How long an audio file really plays (ffprobe)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


# ---------------------------------------------------------------------- the text the voice reads


def test_tags_go_where_the_tone_changes_and_capitals_on_stressed_english_words() -> None:
    words = ["Why", "is", "it", "warm?", "It", "moves", "heat.", "That", "is", "the", "key."]
    deliveries = (Delivery(0, "curious"), Delivery(4, "thoughtful"), Delivery(7, "thoughtful", "slow"))
    text = render(words, frozenset({5, 10}), deliveries, tags=True)
    assert text == "[curious] Why is it warm? [thoughtfully] It MOVES heat. That is the KEY."
    assert render(words, frozenset({5}), deliveries, tags=False) == " ".join(words)  # other models
    hindi = render(
        ["याद", "रखो,", "timeframe", "देखो।"], frozenset({0, 2}), (Delivery(0, "neutral"),), tags=True
    )
    assert hindi == "याद रखो, TIMEFRAME देखो।"  # no tag for a neutral sentence, no capitals for Devanagari
    assert (
        tags_supported("eleven_v4")
        and tags_supported("eleven_v3")
        and not tags_supported("eleven_flash_v2_5")
    )


def test_a_teacher_pauses_at_full_stops_commas_and_before_a_key_point() -> None:
    shaping = Shaping(sentence_pause=0.4, comma_pause=0.18, key_pause=0.35)
    least = plan_pauses(["Look,", "the", "answer", "is", "nine.", "Done!"], frozenset({4}), shaping)
    assert least == [0.18, 0.0, 0.0, 0.35, 0.4, 0.0]
    assert plan_pauses(["नहीं।", "Timeframe"], frozenset(), shaping)[0] == 0.4  # a danda ends a sentence too


# ---------------------------------------------------------------------- a teacher's timing on the audio


def fake_speech(words: list[str], spw: float = 0.3) -> tuple[Speech, list[WordMark]]:
    """Silence with a word every `spw` seconds and 0.06 s between them: a voice running on."""
    marks = [
        WordMark(w, round(0.1 + spw * i, 3), round(0.1 + spw * (i + 0.8), 3)) for i, w in enumerate(words)
    ]
    duration = round(0.2 + spw * len(words), 3)
    return Speech(silent_wav(duration), "wav", duration, tuple(marks)), marks


@needs_ffmpeg
def test_short_pauses_are_lengthened_and_every_word_time_moves_with_the_audio() -> None:
    words = ["One", "two.", "Three", "four,", "five."]
    speech, marks = fake_speech(words)
    shaped = shape(speech, marks, words, shaping=Shaping(0.4, 0.18, 0.35, 0.9))
    assert shaped is not None
    out, moved = shaped
    assert out.format == "mp3" and out.duration_s == pytest.approx(speech.duration_s + 0.34 + 0.12, abs=0.06)
    gaps = [moved[i + 1].start - moved[i].end for i in range(len(moved) - 1)]
    assert gaps[1] == pytest.approx(0.4, abs=0.02) and gaps[3] == pytest.approx(0.18, abs=0.02)
    assert gaps[0] == pytest.approx(0.06, abs=0.01)  # no punctuation, no pause added
    assert [m.text for m in moved] == words


@needs_ffmpeg
def test_a_slow_sentence_is_stretched_and_a_key_point_gets_its_pause() -> None:
    words = ["Look.", "Energy", "is", "conserved", "always."]
    speech, marks = fake_speech(words)
    deliveries = (Delivery(0), Delivery(1, pace="slow"))
    shaped = shape(speech, marks, words, frozenset({3}), deliveries, Shaping(0.4, 0.18, 0.35, 0.9))
    assert shaped is not None
    _, moved = shaped
    before = marks[4].end - marks[1].start
    after_slow = (moved[2].end - moved[1].start) / (marks[2].end - marks[1].start)
    assert after_slow == pytest.approx(1 / 0.9, abs=0.05)  # 10% slower
    assert moved[3].start - moved[2].end >= 0.33  # the pause before the key word
    assert moved[4].end - moved[1].start > before


def test_nothing_to_shape_leaves_the_speech_as_it_is() -> None:
    words = ["One", "two", "three"]
    speech, marks = fake_speech(words)
    assert shape(speech, marks, words, shaping=Shaping()) is None  # no punctuation, nothing slow


# ---------------------------------------------------------------------- the script directs the delivery


def directed_reply(req: LLMRequest) -> dict[str, Any]:
    text = req.messages[0].parts[0].text  # type: ignore[union-attr]
    out = []
    for n, line in enumerate(re.findall(r"Caption \d+ \(.*?\): (.*)", text), 1):
        words = re.findall(r"\[\d+\] (\S+)", line)
        out.append(
            {
                "n": n,
                "tone": "curious" if line.endswith("?") else "thoughtful",
                "pace": "slow" if "equals" in line else "normal",
                "spoken": [w.upper() for w in words],
                "stress": [2, 3, 1],  # three: only two are kept
                "pause_before": [1, 3],  # a pause before the first word is none
            }
        )
    return {"captions": out}


async def test_the_script_directs_the_delivery_and_sees_what_is_on_screen(tmp_path: Path) -> None:
    calls: list[LLMRequest] = []

    def handler(req: LLMRequest) -> Any:
        calls.append(req)
        return directed_reply(req)

    scripter = SpeechScripter(
        FakeLLM(handler), settings(animator_model_lightweight="m-light"), tmp_path, directed=True
    )
    texts = ["Two plus three equals five.", "What is **energy**?"]
    got = await scripter.scripts(texts, "English", None, cues={texts[1]: "the word energy glows"})
    first = got[texts[0]]
    assert first.spoken == ("Two", "plus", "three", "equals", "five.")  # English keeps its words
    assert first.stress == frozenset({1, 2}) and first.pause_before == frozenset({2})
    assert first.deliveries == (Delivery(0, "thoughtful", "slow"),)
    assert got[texts[1]].deliveries[0].tone == "curious"
    message = calls[0].messages[0].parts[0].text  # type: ignore[union-attr]
    assert "on screen: the word energy glows" in message and calls[0].prompt_version == "speech-script.v2"
    hinglish = await scripter.scripts(["Dekho yeh **ball**."], "Hinglish", None)
    assert hinglish["Dekho yeh **ball**."].spoken == ("DEKHO", "YEH", "BALL.")  # Hinglish is rewritten
    again = SpeechScripter(
        FakeLLM(handler), settings(animator_model_lightweight="m-light"), tmp_path, directed=True
    )
    assert (await again.scripts(texts, "English", None))[texts[0]] == first and len(calls) == 2  # cached


def test_a_take_keeps_each_sentences_delivery() -> None:
    a = Script(("One", "two."), "curious", frozenset({1}), frozenset(), (Delivery(0, "curious"),))
    b = Script(
        ("Three", "four."), "serious", frozenset({0}), frozenset({1}), (Delivery(0, "serious", "slow"),)
    )
    take = take_script(["One two.", "Plain words.", "Three four."], {"One two.": a, "Three four.": b})
    assert take is not None
    assert take.spoken == ("One", "two.", "Plain", "words.", "Three", "four.")
    assert take.stress == frozenset({1, 4}) and take.pause_before == frozenset({5})
    assert take.deliveries == (Delivery(0, "curious"), Delivery(2), Delivery(4, "serious", "slow"))


# ---------------------------------------------------------------------- the speech cache


@needs_ffmpeg
async def test_the_cache_speaks_the_delivery_and_keeps_the_captions_in_time(tmp_path: Path) -> None:
    tts = FakeTTS(seconds_per_word=0.3)
    plain = await SpeechCache(tts, tmp_path / "plain").speak("Look here. Now, see.")
    directed = SpeechCache(tts, tmp_path / "directed", prosody=Shaping())
    script = Script(("Look", "here.", "Now,", "see."), "neutral", frozenset({3}), frozenset(), (Delivery(0),))
    spoken = await directed.speak("Look here. Now, see.", script=script)
    assert tts.calls[-1] == "Look here. Now, see."  # the fake voice takes no tags
    assert spoken.duration_s > plain.duration_s + 0.3  # the pauses a teacher makes
    assert spoken.marks is not None and [m.text for m in spoken.marks] == caption_words(
        "Look here. Now, see."
    )
    assert spoken.marks[2].start - spoken.marks[1].end == pytest.approx(0.4, abs=0.03)
    # The file the player plays is the shaped speech the word times belong to (the voice and the
    # caption highlight stay together to the last word).
    saved = tmp_path / "directed" / spoken.file
    assert saved.suffix == ".mp3" and audio_seconds(saved) == pytest.approx(spoken.duration_s, abs=0.06)
    assert spoken.marks[-1].end <= audio_seconds(saved)
    again = await SpeechCache(FakeTTS(seconds_per_word=0.3), tmp_path / "directed", prosody=Shaping()).speak(
        "Look here. Now, see.", script=script
    )
    assert again.file == spoken.file  # from disk


async def test_tags_reach_eleven_v4_and_never_become_caption_words() -> None:
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

    v4 = ElevenLabsTTS("k", "v", "eleven_v4", transport=mock(handler))
    speech = await v4.synthesize("[curious] Kya hoga? [short pause] Dekho NA.")
    assert speech.marks is not None and [m.text for m in speech.marks] == ["Kya", "hoga?", "Dekho", "NA."]
    assert bodies[0]["voice_settings"] == {"stability": 0.5} and "previous_text" not in bodies[0]


# ---------------------------------------------------------------------- voice and picture together


def test_a_highlight_lands_on_the_word_that_names_its_thing() -> None:
    dsl = {
        "timeline": [
            {
                "id": "h1",
                "action": "highlight",
                "target": "high_label",
                "start": {"at": "beat:b1", "delay": 4.0},
            },
            {
                "id": "h2",
                "action": "highlight",
                "target": "low_label",
                "start": {"at": "beat:b1", "delay": 0.5},
            },
            {
                "id": "h3",
                "action": "highlight",
                "target": "box_main",
                "start": {"at": "beat:b1", "delay": 1.0},
            },
            {"id": "h4", "action": "highlight", "target": "high_label", "start": {"after": "h1"}},
        ]
    }
    words = {"b1": [("The", 0.0), ("high", 0.6), ("and", 1.0), ("the", 1.2), ("low,", 1.5), ("High!", 2.4)]}
    out = sync_highlights(dsl, words, {"b1": frozenset({5})})
    assert out is not None
    timeline = {a["id"]: a for a in out["timeline"]}
    assert timeline["h1"]["start"]["delay"] == 2.25  # the stressed "High!" (2.4 s), a moment before
    assert timeline["h2"]["start"]["delay"] == 1.35  # "low," at 1.5 s
    assert timeline["h3"]["start"]["delay"] == 1.0  # its thing is not named: unchanged
    assert timeline["h4"]["start"] == {"after": "h1"}  # not started from a beat: unchanged
    assert sync_highlights(out, words, {"b1": frozenset({5})}) is None  # already in place
