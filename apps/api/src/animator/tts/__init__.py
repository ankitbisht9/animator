"""Narration audio (text to speech), one switch: ANIMATOR_TTS_ENABLED (ADR-037).

`build_speech(settings)` returns the speech cache over the configured provider, or None when the
switch is off or the provider is not usable (a missing key is logged, never fatal). With None,
nothing in the pipeline, the API, the player or the export speaks or changes.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from animator.config import Settings
from animator.tts.cache import SpeechCache, Spoken
from animator.tts.providers import (
    AzureTTS,
    EdgeTTS,
    ElevenLabsTTS,
    FakeTTS,
    GoogleTTS,
    KokoroTTS,
    OpenAICompatibleTTS,
)
from animator.tts.text import caption_words, speech_text, strip_markup, word_times
from animator.tts.types import Speech, TTSError, TTSProvider, WordMark

__all__ = [
    "AzureTTS",
    "EdgeTTS",
    "ElevenLabsTTS",
    "FakeTTS",
    "GoogleTTS",
    "KokoroTTS",
    "OpenAICompatibleTTS",
    "Speech",
    "SpeechCache",
    "Spoken",
    "TTSError",
    "TTSProvider",
    "WordMark",
    "build_provider",
    "build_speech",
    "caption_words",
    "env_duplicates",
    "speech_text",
    "strip_markup",
    "tts_problem",
    "word_times",
]

log = logging.getLogger("animator.tts")


def _secret(value: object) -> str:
    get = getattr(value, "get_secret_value", None)
    return str(get()).strip() if get is not None else ""


def build_provider(s: Settings) -> TTSProvider:
    """The configured provider (raises TTSError when its key or settings are missing)."""
    p = s.animator_tts_provider
    voice, speed, timeout = s.animator_tts_voice.strip(), s.animator_tts_speed, s.animator_tts_timeout_s
    if p == "edge":
        return EdgeTTS(voice, speed, timeout)
    if p == "azure":
        return AzureTTS(_secret(s.azure_speech_key), s.azure_speech_region.strip(), voice, speed, timeout)
    if p == "google":
        return GoogleTTS(_secret(s.google_tts_api_key), voice, speed, timeout)
    if p == "elevenlabs":
        return ElevenLabsTTS(
            _secret(s.elevenlabs_api_key),
            voice,
            s.animator_tts_model.strip(),
            speed,
            timeout,
            stability=s.animator_tts_stability,
            style=s.animator_tts_style,
        )
    if p == "kokoro":
        return KokoroTTS(s.animator_tts_base_url.strip(), voice, speed, timeout)
    if p == "openai":
        base = s.animator_tts_base_url.strip()
        key = _secret(s.animator_tts_api_key) or ("" if base else _secret(s.openai_api_key))
        return OpenAICompatibleTTS(
            key,
            base,
            voice,
            s.animator_tts_model.strip(),
            speed,
            s.animator_tts_instructions.strip(),
            timeout,
        )
    if p == "fake":
        return FakeTTS()
    raise TTSError(f"unknown ANIMATOR_TTS_PROVIDER '{p}'")


def tts_problem(s: Settings) -> str | None:
    """Why narration is off although the switch is on (for /api/config), or None."""
    if not s.animator_tts_enabled:
        return None
    try:
        build_provider(s)
    except TTSError as e:
        return str(e)
    return None


_TTS_KEYS = re.compile(
    r"^\s*(ANIMATOR_TTS_\w+|AZURE_SPEECH_\w+|GOOGLE_TTS_\w+|ELEVENLABS_\w+)\s*=", re.MULTILINE
)


def env_duplicates(env_file: Path | None) -> list[str]:
    """Narration settings written more than once in `.env`: the LAST one wins, which silently undoes
    the first (an empty `ANIMATOR_TTS_BASE_URL=` below a filled one, for example)."""
    try:
        text = env_file.read_text(encoding="utf-8") if env_file is not None else ""
    except OSError:
        return []
    names = [m.group(1) for m in _TTS_KEYS.finditer(text)]
    return sorted({n for n in names if names.count(n) > 1})


def build_speech(
    s: Settings, provider: TTSProvider | None = None, env_file: Path | None = None
) -> SpeechCache | None:
    if not s.animator_tts_enabled:
        return None
    for name in env_duplicates(env_file):
        log.warning("%s is set more than once in .env: only the last one counts", name)
    if provider is None:
        try:
            provider = build_provider(s)
        except TTSError as e:
            log.warning("narration audio is off: %s", e)
            return None
    voices = {"hinglish": s.animator_tts_voice_hinglish.strip()}
    providers: dict[str, TTSProvider] = {}
    other = s.animator_tts_provider_hinglish
    if other and other != provider.name:
        try:
            # The Hinglish provider speaks only Hinglish lessons: its own default voice, not
            # ANIMATOR_TTS_VOICE (which names a voice of the main provider).
            providers["hinglish"] = build_provider(
                s.model_copy(update={"animator_tts_provider": other, "animator_tts_voice": ""})
            )
        except TTSError as e:
            log.warning(
                "Hinglish lessons use %s: ANIMATOR_TTS_PROVIDER_HINGLISH=%s is not usable (%s)",
                provider.name,
                other,
                e,
            )
    speech = SpeechCache(
        provider,
        s.resolve(s.animator_tts_cache_dir),
        s.animator_tts_concurrency,
        voices=voices,
        providers=providers,
    )
    log.info(
        "narration audio: %s, voice %s (Hinglish: %s, voice %s)",
        provider.name,
        provider.voice,
        speech.provider_for("Hinglish").name,
        speech.voice_for("Hinglish"),
    )
    return speech
