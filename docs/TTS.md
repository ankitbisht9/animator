# Narration audio (TTS): integration plan

Status: designed, not implemented. Lessons currently ship with on-screen captions only (spec Section 16).

## What already exists

- Narration is per-beat text in every scene (`narration.beats`), with `**emphasis**` markup. Actions start from
  beats (`"at": "beat:b2"`) rather than from absolute times, so beats can be retimed without rewriting the timeline.
- The exporter has an audio hook: `AudioSource.track(lesson, duration, workdir) -> Path | None`. The track is muxed
  as AAC and padded or trimmed to the video's exact duration (`animator.export.exporter`, tested).
- The player shows captions with word-by-word highlighting.

## Plan

1. **`TTSProvider` interface** (Python, next to `LLMClient`):
   `async synthesize(text, voice, language) -> Speech(audio: bytes, format, duration_s, words: list[(word, start, end)] | None)`.
   Implementations: OpenAI TTS, a fake for tests, and record/replay like the LLM layer. Model and voice are
   configuration (`ANIMATOR_TTS_MODEL`, `ANIMATOR_TTS_VOICE`); prices go in `config/pricing.json`.
2. **Synthesis per beat**, right after a scene is READY. Strip the emphasis markup for speech and cache by
   (text, voice, model). Store the audio next to the generation (`var/generations/<id>/audio/`).
3. **`retime(scene, beat_durations)` in the engine** (TypeScript, one implementation, P8). It sets each beat's
   duration to its audio duration plus a small pad, shifts beat-anchored actions, and grows the scene duration when
   speech runs long. The worker exposes it as `scene.retime`; the retimed DSL is validated like any other.
4. **Playback.** The player schedules each beat's audio at the beat's start from the same global clock, with seek,
   pause and speed support; captions can use the provider's word timestamps.
5. **Export.** An `AudioSource` concatenates the beat audio at the beat start times, with silence between. The
   existing hook muxes it into the MP4.
6. **Tests.** Retime keeps order and relative offsets; long speech grows the scene; the fake provider gives
   deterministic durations; export with audio has the exact video duration.
