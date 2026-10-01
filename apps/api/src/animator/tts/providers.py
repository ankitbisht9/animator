"""Text-to-speech providers. Each one takes plain text and returns `Speech`; nothing else in the
project knows which provider is in use (ADR-037).

- `edge`: Microsoft's neural voices through the Edge "read aloud" service (edge-tts). Free, no key
  or account; word timings included. Unofficial: Microsoft may change or limit it.
- `azure`: the same voices through the official Azure Speech service (free F0 tier: 500,000
  characters a month). Needs AZURE_SPEECH_KEY and AZURE_SPEECH_REGION. Word timings estimated.
- `google`: Google Cloud Text-to-Speech (free tier: 1M characters a month for Neural2 voices, 4M
  for Standard). Needs GOOGLE_TTS_API_KEY. Word timings from SSML marks.
- `elevenlabs`: the most expressive voices (free tier: 10,000 credits a month, non-commercial).
  Needs ELEVENLABS_API_KEY. Word timings from character alignment.
- `kokoro`: Kokoro-82M (open weights, Apache-2.0) on a local Kokoro-FastAPI server. Free and
  offline; word timings included. 72 voices: American and British English, Hindi, and more.
- `openai`: any OpenAI-compatible `/audio/speech` server: OpenAI itself (paid, uses
  OPENAI_API_KEY) or a free local one such as Kokoro-FastAPI (ANIMATOR_TTS_BASE_URL). Word
  timings estimated.

`voice` is a provider's default voice; `synthesize(text, voice)` may use another one (a Hinglish
lesson uses `hinglish_voice`, ADR-038). Keys are read from settings and sent only to their
provider; no error message contains them.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any, ClassVar
from xml.sax.saxutils import escape

import edge_tts
import httpx2

from animator.tts.audio import mp3_duration, silent_wav
from animator.tts.types import Speech, SpeechContext, TTSError, WordMark


def _percent(speed: float) -> str:
    return f"{round((speed - 1.0) * 100):+d}%"


def _language(voice: str, default: str = "en-US") -> str:
    """ "en-US-AvaMultilingualNeural" -> "en-US"."""
    parts = voice.split("-")
    return "-".join(parts[:2]) if len(parts) >= 3 else default


def _detail(r: httpx2.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return r.text[:200]
    if isinstance(body, dict):
        err = body.get("error") or body.get("detail") or body
        if isinstance(err, dict):
            err = err.get("message") or err.get("status") or err
        return str(err)[:200]
    return str(body)[:200]


class _Http:
    """A provider that talks to an HTTP API (one shared connection pool)."""

    name = "http"

    def __init__(self, timeout_s: float, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._timeout_s = timeout_s
        self._transport = transport
        self._client: httpx2.AsyncClient | None = None  # opened on first use

    async def _post(self, url: str, **kw: Any) -> httpx2.Response:
        if self._client is None:
            self._client = httpx2.AsyncClient(timeout=self._timeout_s, transport=self._transport)
        try:
            r = await self._client.post(url, **kw)
        except httpx2.HTTPError as e:
            raise TTSError(f"{self.name}: {type(e).__name__}") from e
        if r.status_code != 200:
            raise TTSError(f"{self.name}: HTTP {r.status_code}: {_detail(r)}")
        return r

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()


class EdgeTTS:
    name = "edge"
    DEFAULT_VOICE = "en-US-AvaMultilingualNeural"
    # Native Hindi, female: reads Roman-script Hinglish with an Indian accent (ADR-039).
    HINGLISH_VOICE = "hi-IN-SwaraNeural"

    def __init__(self, voice: str = "", speed: float = 1.0, timeout_s: float = 30.0) -> None:
        self.voice = voice or self.DEFAULT_VOICE
        self.hinglish_voice = self.HINGLISH_VOICE
        self.model = ""
        self.rate = _percent(speed)
        self.timeout_s = timeout_s

    def cache_key(self, voice: str) -> str:
        return f"edge|{voice}|{self.rate}"

    async def synthesize(
        self, text: str, voice: str | None = None, context: SpeechContext | None = None
    ) -> Speech:
        audio = bytearray()
        marks: list[WordMark] = []
        try:
            async with asyncio.timeout(self.timeout_s):
                comm = edge_tts.Communicate(
                    text, voice or self.voice, rate=self.rate, boundary="WordBoundary"
                )
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        audio += chunk["data"]
                    elif chunk["type"] == "WordBoundary":
                        start = chunk["offset"] / 1e7
                        marks.append(WordMark(chunk["text"], start, start + chunk["duration"] / 1e7))
        except TimeoutError as e:
            raise TTSError(f"edge: no answer within {self.timeout_s:.0f} s") from e
        except TTSError:
            raise
        except Exception as e:  # the library raises its own and aiohttp's errors
            raise TTSError(f"edge: {type(e).__name__}: {e}") from e
        if not audio:
            raise TTSError("edge: no audio received")
        data = bytes(audio)
        return Speech(data, "mp3", mp3_duration(data), tuple(marks))

    async def close(self) -> None:
        return None


class AzureTTS(_Http):
    name = "azure"
    DEFAULT_VOICE = "en-US-AvaMultilingualNeural"

    def __init__(
        self,
        api_key: str,
        region: str,
        voice: str = "",
        speed: float = 1.0,
        timeout_s: float = 30.0,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key or not region:
            raise TTSError("azure: set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION")
        super().__init__(timeout_s, transport)
        self._api_key = api_key
        self.region = region
        self.voice = voice or self.DEFAULT_VOICE
        self.hinglish_voice = EdgeTTS.HINGLISH_VOICE  # the same voices as edge
        self.model = ""
        self.rate = _percent(speed)

    def cache_key(self, voice: str) -> str:
        return f"azure|{voice}|{self.rate}"

    async def synthesize(
        self, text: str, voice: str | None = None, context: SpeechContext | None = None
    ) -> Speech:
        v = voice or self.voice
        ssml = (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xml:lang="{_language(v)}"><voice name="{escape(v)}">'
            f'<prosody rate="{self.rate}">{escape(text)}</prosody></voice></speak>'
        )
        r = await self._post(
            f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1",
            content=ssml.encode("utf-8"),
            headers={
                "Ocp-Apim-Subscription-Key": self._api_key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                "User-Agent": "animator",
            },
        )
        return Speech(r.content, "mp3", mp3_duration(r.content), None)


class GoogleTTS(_Http):
    name = "google"
    DEFAULT_VOICE = "en-US-Neural2-F"
    HINGLISH_VOICE = "en-IN-Neural2-A"  # Indian English, female
    URL = "https://texttospeech.googleapis.com/v1beta1/text:synthesize"

    def __init__(
        self,
        api_key: str,
        voice: str = "",
        speed: float = 1.0,
        timeout_s: float = 30.0,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise TTSError("google: set GOOGLE_TTS_API_KEY")
        super().__init__(timeout_s, transport)
        self._api_key = api_key
        self.voice = voice or self.DEFAULT_VOICE
        self.hinglish_voice = self.HINGLISH_VOICE
        self.model = ""
        self.speed = speed

    def cache_key(self, voice: str) -> str:
        return f"google|{voice}|{self.speed}"

    async def synthesize(
        self, text: str, voice: str | None = None, context: SpeechContext | None = None
    ) -> Speech:
        v = voice or self.voice
        words = text.split()
        # A mark before every word: the reply says when each one starts.
        marked = " ".join(f'<mark name="w{i}"/>{escape(w)}' for i, w in enumerate(words))
        r = await self._post(
            self.URL,
            headers={"X-Goog-Api-Key": self._api_key},
            json={
                "input": {"ssml": f"<speak>{marked}</speak>"},
                "voice": {"languageCode": _language(v), "name": v},
                "audioConfig": {"audioEncoding": "MP3", "speakingRate": self.speed, "sampleRateHertz": 24000},
                "enableTimePointing": ["SSML_MARK"],
            },
        )
        data = r.json()
        audio = base64.b64decode(data.get("audioContent", ""))
        if not audio:
            raise TTSError("google: no audio received")
        duration = mp3_duration(audio)
        starts: dict[int, float] = {}
        for tp in data.get("timepoints") or []:
            name = str(tp.get("markName", ""))
            if name.startswith("w") and name[1:].isdigit():
                starts[int(name[1:])] = float(tp.get("timeSeconds", 0.0))
        marks = []
        order = sorted(starts)
        for k, i in enumerate(order):
            end = starts[order[k + 1]] if k + 1 < len(order) else duration
            if i < len(words):
                marks.append(WordMark(words[i], starts[i], max(starts[i], end)))
        return Speech(audio, "mp3", duration, tuple(marks) if marks else None)


class ElevenLabsTTS(_Http):
    name = "elevenlabs"
    DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # "George", a premade voice every account has
    DEFAULT_MODEL = "eleven_multilingual_v2"
    # A sentence's tone as an Eleven v3 audio tag (other models take no tags).
    TONE_TAGS: ClassVar[dict[str, str]] = {
        "curious": "[curious]",
        "warm": "[warmly]",
        "encouraging": "[encouraging]",
        "excited": "[excited]",
        "thoughtful": "[thoughtful]",
        "serious": "[serious]",
    }

    def __init__(
        self,
        api_key: str,
        voice: str = "",
        model: str = "",
        speed: float = 1.0,
        timeout_s: float = 30.0,
        transport: httpx2.AsyncBaseTransport | None = None,
        stability: float = 0.4,
        style: float = 0.3,
    ) -> None:
        if not api_key:
            raise TTSError("elevenlabs: set ELEVENLABS_API_KEY")
        super().__init__(timeout_s, transport)
        self._api_key = api_key
        self.voice = voice or self.DEFAULT_VOICE
        self.hinglish_voice = self.voice  # multilingual models speak Hindi in the same voice
        self.model = model or self.DEFAULT_MODEL
        self.speed = min(1.2, max(0.7, speed))  # the range the API accepts
        # Lower stability and some style: a livelier, more human delivery (ElevenLabs' defaults are
        # flatter). Eleven v3 takes only 0.0 (creative), 0.5 (natural) or 1.0 (robust).
        self.stability = min(1.0, max(0.0, stability))
        self.style = min(1.0, max(0.0, style))

    @property
    def v3(self) -> bool:
        """Eleven v3 and v4: audio tags, stability 0 / 0.5 / 1, no request stitching."""
        return self.model.startswith(("eleven_v3", "eleven_v4"))

    def cache_key(self, voice: str) -> str:
        return f"elevenlabs|{self.model}|{voice}|{self.speed}|{self.stability}|{self.style}"

    def _settings(self) -> dict[str, Any]:
        if self.v3:
            return {"stability": min((0.0, 0.5, 1.0), key=lambda v: abs(v - self.stability))}
        return {
            "stability": self.stability,
            "similarity_boost": 0.75,
            "style": self.style,
            "use_speaker_boost": True,
            "speed": self.speed,
        }

    async def synthesize(
        self, text: str, voice: str | None = None, context: SpeechContext | None = None
    ) -> Speech:
        body: dict[str, Any] = {"text": text, "model_id": self.model, "voice_settings": self._settings()}
        if context is not None:
            if self.v3 and context.tone in self.TONE_TAGS:
                body["text"] = f"{self.TONE_TAGS[context.tone]} {text}"
            # The sentences around this one: the voice carries on from the last and leads into the
            # next, instead of starting each sentence fresh (ElevenLabs request stitching).
            if context.previous and not self.v3:
                body["previous_text"] = context.previous
            if context.following and not self.v3:
                body["next_text"] = context.following
        r = await self._post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice or self.voice}/with-timestamps",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": self._api_key},
            json=body,
        )
        data = r.json()
        audio = base64.b64decode(data.get("audio_base64", ""))
        if not audio:
            raise TTSError("elevenlabs: no audio received")
        al = data.get("alignment") or {}
        chars = al.get("characters") or []
        t0 = al.get("character_start_times_seconds") or []
        t1 = al.get("character_end_times_seconds") or []
        marks: list[WordMark] = []
        word, ws, we = "", 0.0, 0.0
        tag = False  # inside an audio tag ("[short pause]"): not spoken words
        for ch, s, e in zip(chars, t0, t1, strict=False):
            if ch == "[":
                tag = True
            if tag:
                tag = ch != "]"
                continue
            if ch.isspace():
                if word:
                    marks.append(WordMark(word, ws, we))
                word = ""
                continue
            if not word:
                ws = float(s)
            word += ch
            we = float(e)
        if word:
            marks.append(WordMark(word, ws, we))
        # Audio tags are not spoken words.
        marks = [m for m in marks if not (m.text.startswith("[") and m.text.endswith("]"))]
        return Speech(audio, "mp3", mp3_duration(audio), tuple(marks) if marks else None)


class KokoroTTS(_Http):
    """Kokoro-82M on a local Kokoro-FastAPI server (`/dev/captioned_speech`: audio and the time of
    every word). Voice names start with the language and gender: `af_` American female, `am_`
    American male, `bf_`/`bm_` British, `hf_`/`hm_` Hindi (which also reads Roman-script Hinglish)."""

    name = "kokoro"
    DEFAULT_BASE = "http://localhost:8880"
    DEFAULT_VOICE = "af_heart"  # the best-graded Kokoro voice (A)
    # Kokoro reads Roman letters with English sounds even in its Hindi voices, so Hinglish sounds
    # English-accented; ANIMATOR_TTS_PROVIDER_HINGLISH=edge gives an Indian voice (ADR-039).
    HINGLISH_VOICE = "hf_alpha"

    def __init__(
        self,
        base_url: str = "",
        voice: str = "",
        speed: float = 1.0,
        timeout_s: float = 60.0,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(timeout_s, transport)
        base = (base_url or self.DEFAULT_BASE).rstrip("/")
        self.base_url = base[:-3] if base.endswith("/v1") else base  # the OpenAI-style URL works too
        self.voice = voice or self.DEFAULT_VOICE
        self.hinglish_voice = self.HINGLISH_VOICE
        self.model = "kokoro"
        self.speed = speed

    def cache_key(self, voice: str) -> str:
        return f"kokoro|{voice}|{self.speed}"

    async def synthesize(
        self, text: str, voice: str | None = None, context: SpeechContext | None = None
    ) -> Speech:
        r = await self._post(
            f"{self.base_url}/dev/captioned_speech",
            json={
                "model": "kokoro",
                "input": text,
                "voice": voice or self.voice,
                "response_format": "mp3",
                "speed": self.speed,
                "stream": False,
                "return_timestamps": True,
            },
        )
        data = r.json()
        audio = base64.b64decode(data.get("audio", ""))
        if not audio:
            raise TTSError("kokoro: no audio received")
        marks = tuple(
            WordMark(str(t.get("word", "")), float(t.get("start_time", 0.0)), float(t.get("end_time", 0.0)))
            for t in data.get("timestamps") or []
        )
        return Speech(audio, "mp3", mp3_duration(audio), marks or None)


class OpenAICompatibleTTS(_Http):
    """POST {base}/audio/speech: OpenAI, or a local server with the same API (Kokoro-FastAPI)."""

    name = "openai"
    DEFAULT_BASE = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-4o-mini-tts"
    DEFAULT_VOICE = "coral"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        voice: str = "",
        model: str = "",
        speed: float = 1.0,
        instructions: str = "",
        timeout_s: float = 30.0,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or self.DEFAULT_BASE).rstrip("/")
        if not api_key and self.base_url == self.DEFAULT_BASE:
            raise TTSError("openai: set OPENAI_API_KEY (or ANIMATOR_TTS_API_KEY)")
        if model == "kokoro" and self.base_url == self.DEFAULT_BASE:
            raise TTSError(
                "openai: model 'kokoro' needs ANIMATOR_TTS_BASE_URL (your Kokoro server), "
                "or use ANIMATOR_TTS_PROVIDER=kokoro"
            )
        super().__init__(timeout_s, transport)
        self._api_key = api_key
        self.voice = voice or self.DEFAULT_VOICE
        self.hinglish_voice = self.voice
        self.model = model or self.DEFAULT_MODEL
        self.speed = speed
        self.instructions = instructions

    def cache_key(self, voice: str) -> str:
        return f"openai|{self.base_url}|{self.model}|{voice}|{self.speed}|{self.instructions}"

    async def synthesize(
        self, text: str, voice: str | None = None, context: SpeechContext | None = None
    ) -> Speech:
        body: dict[str, Any] = {
            "model": self.model,
            "voice": voice or self.voice,
            "input": text,
            "response_format": "mp3",
            "speed": self.speed,
        }
        if self.instructions and self.model.startswith("gpt-"):
            body["instructions"] = self.instructions  # tone of voice (gpt-4o-mini-tts)
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        r = await self._post(f"{self.base_url}/audio/speech", json=body, headers=headers)
        if not r.content:
            raise TTSError("openai: no audio received")
        return Speech(r.content, "mp3", mp3_duration(r.content), None)


class FakeTTS:
    """Deterministic offline speech for tests: silence, `seconds_per_word` per word, with marks."""

    name = "fake"

    def __init__(self, seconds_per_word: float = 0.3, fail: bool = False, delay_s: float = 0.0) -> None:
        self.voice = "fake"
        self.hinglish_voice = "fake_hinglish"
        self.model = ""
        self.seconds_per_word = seconds_per_word
        self.fail = fail
        self.delay_s = delay_s
        self.calls: list[str] = []
        self.voices: list[str] = []
        self.contexts: list[SpeechContext | None] = []

    def cache_key(self, voice: str) -> str:
        return f"fake|{voice}|{self.seconds_per_word}"

    async def synthesize(
        self, text: str, voice: str | None = None, context: SpeechContext | None = None
    ) -> Speech:
        self.calls.append(text)
        self.voices.append(voice or self.voice)
        self.contexts.append(context)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.fail:
            raise TTSError("fake: failing on purpose")
        words = text.split()
        spw = self.seconds_per_word
        duration = round(0.1 + spw * len(words) + 0.1, 3)
        marks = tuple(
            WordMark(w, round(0.1 + spw * i, 3), round(0.1 + spw * (i + 0.8), 3)) for i, w in enumerate(words)
        )
        return Speech(silent_wav(duration), "wav", duration, marks)

    async def close(self) -> None:
        return None
