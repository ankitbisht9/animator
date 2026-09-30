# Narration voice (text to speech)

Every narration beat (the sentences shown in the caption bar) is spoken by a neural voice. Each caption word lights
up exactly when the voice says it, in the player and in exported MP4s. See ADR-037 for the design decisions.

## The switch

One line in `.env` controls everything:

```
ANIMATOR_TTS_ENABLED=1   # the lesson is spoken
ANIMATOR_TTS_ENABLED=0   # no voice anywhere
```

With `0` the project behaves exactly as it did before this feature:
- no speech is synthesized;
- scenes keep the timing the model wrote;
- the API sends no narration and serves no audio (`/api/tts/...` returns 404);
- the player shows no voice button and times captions on its own;
- exports have no audio track.

This also holds for lessons that were made with a voice. Restart the API (`make dev`) after changing `.env`.

## How it works

1. **While a scene is being written**, the pipeline already synthesizes the storyboard's sentences
   (`SpeechCache.prefetch`). The model copies those sentences into the scene, so the speech is usually ready before
   the scene is. Measured cost of the voice step: 0.02-0.07 s per scene.
2. **When a scene is valid**, every beat is spoken (cached by content: the same sentence in the same voice is
   synthesized only once, across drafts, scenes and lessons). Files go to `var/tts/<id>.mp3`, plus
   `<id>.json` holding the duration and word timings.
3. **Retiming.** A beat whose speech is longer than the beat is stretched to fit (speech, then a 0.35 s pause):
   - times inside a stretched beat move proportionally;
   - later beats and the scene end move later;
   - beat references, `after`/`with` chains, delays and motion durations are unchanged.
   The retimed scene is validated by the engine again, and the original is kept if that fails. Beats are never
   shortened: the pictures keep the time the model gave them.
4. **Word timings.** The provider's word marks are matched to the caption's words by their letters and digits.
   A mark that covers several words ("9.8 m/s²" said as one unit) is shared between them. Words said differently
   ("twelve" for "12"), and every word from providers without marks, are estimated from word length and
   punctuation pauses within the real audio duration.
5. **Player.** The beat on screen plays its audio from the player's clock. Pause, seek, speed (0.5x-2x),
   checkpoints and "preparing the next scene" all follow that clock, and the audio is re-synced if it drifts
   by more than 0.25 s. The speaker button (or the `m` key) turns the voice off and on for the viewer.
6. **Export.** Each beat's audio is placed at its time in the lesson, muxed as AAC, and padded to the exact video
   length. The captions in the video follow the voice word by word.
7. **Failures never break a lesson.** If speech fails (network, quota, bad key), that scene keeps captions only,
   and the lesson's `tts_failures` metric counts it. A provider with a missing key is reported in `/api/config` and
   in the API log; lessons are still made, without a voice.

Metrics per lesson (Debug page, API): `tts_chars` (characters narrated; free tiers count characters, and cached
sentences are not sent again), `tts_audio_s`, `tts_failures`.

## Providers

`ANIMATOR_TTS_PROVIDER` selects one. Everything else is the same for all of them.

| Provider | Cost | Key | Word timings | Notes |
|---|---|---|---|---|
| `edge` (default) | free | none | exact | Microsoft neural voices through the Edge read-aloud service (edge-tts). Unofficial: Microsoft may change or limit it. |
| `azure` | free tier: 500,000 characters/month (F0) | `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` | estimated | The same voices as `edge`, officially. The fallback if `edge` stops working. |
| `google` | free tier: 1M characters/month on Neural2 voices, 4M on Standard | `GOOGLE_TTS_API_KEY` | exact (SSML marks) | Needs a Google Cloud billing account (not charged within the free tier). |
| `elevenlabs` | free tier: 10,000 credits/month, non-commercial | `ELEVENLABS_API_KEY` | exact | The most expressive, human-sounding voices. |
| `openai` | OpenAI: paid (about $0.015 per minute); local server: free | `OPENAI_API_KEY` (already set) or none | estimated | Any server with OpenAI's `/audio/speech` API, e.g. a local Kokoro. |

Voice and speed: `ANIMATOR_TTS_VOICE` (empty = the provider's default) and `ANIMATOR_TTS_SPEED` (1.0 = normal).
The default `edge` voice, `en-US-AvaMultilingualNeural`, also reads other lesson languages (Spanish, French,
Hindi, ...) in the same voice. Other good `edge`/`azure` voices:
- `en-US-AndrewMultilingualNeural`: warm, male;
- `en-US-EmmaMultilingualNeural`: cheerful;
- `en-GB-SoniaNeural`: British;
- `en-IN-NeerjaNeural`: Indian English.
List all of them with `uv run edge-tts --list-voices`.

### Step by step: Azure Speech (free F0)

1. Sign in at https://portal.azure.com (a free account needs a card for identity; F0 is never charged).
2. **Create a resource** > search "Speech" > **Speech** (Azure AI services) > **Create**.
3. Choose a subscription and resource group, a **Region** (for example `westeurope`), any name, and the
   **Free F0** pricing tier. **Review + create** > **Create**.
4. Open the resource > **Keys and Endpoint**. Copy **KEY 1** and the **Location/Region**.
5. In `.env`: `ANIMATOR_TTS_PROVIDER=azure`, `AZURE_SPEECH_KEY=<KEY 1>`, `AZURE_SPEECH_REGION=<region>`.
6. Restart `make dev`. `/api/config` shows `"tts": {"enabled": true, ...}`.

### Step by step: Google Cloud Text-to-Speech

1. Open https://console.cloud.google.com and create a project (or pick one). Attach a billing account (required
   even for the free tier).
2. **APIs & Services** > **Library** > "Cloud Text-to-Speech API" > **Enable**.
3. **APIs & Services** > **Credentials** > **Create credentials** > **API key**. Click the new key >
   **API restrictions** > **Restrict key** > select "Cloud Text-to-Speech API" > **Save**.
4. In `.env`: `ANIMATOR_TTS_PROVIDER=google`, `GOOGLE_TTS_API_KEY=<the key>`, and optionally
   `ANIMATOR_TTS_VOICE=en-US-Neural2-F` (other voices: https://cloud.google.com/text-to-speech/docs/voices). Use
   Neural2, WaveNet or Standard voices: Chirp 3 HD voices do not report word timings (they are then estimated).
5. Restart `make dev`.

### Step by step: ElevenLabs

1. Sign up at https://elevenlabs.io (free plan).
2. Profile menu (bottom left) > **API Keys** > **Create API Key**. Give it Text to Speech access, then copy it.
3. Optional: pick a voice in **Voices** > **Voice Library**, add it to "My Voices", and copy its **Voice ID**.
4. In `.env`: `ANIMATOR_TTS_PROVIDER=elevenlabs`, `ELEVENLABS_API_KEY=<the key>`, and optionally
   `ANIMATOR_TTS_VOICE=<voice id>`.
   - `ANIMATOR_TTS_MODEL=eleven_flash_v2_5` doubles the free quota (half a credit per character) at slightly
     lower expressiveness. The default is `eleven_multilingual_v2`.
5. Restart `make dev`. A 1-minute lesson uses about 700-900 characters.

### OpenAI TTS (paid, no new key)

`ANIMATOR_TTS_PROVIDER=openai`. It uses `OPENAI_API_KEY`, model `gpt-4o-mini-tts` and voice `coral`. Tone of voice
is set with `ANIMATOR_TTS_INSTRUCTIONS`, for example "Speak like a warm, enthusiastic teacher."

## Free local voice: Kokoro-82M

Kokoro-82M is an open-weight model (Apache-2.0) with 82M parameters. It sounds close to the best cloud voices, runs
faster than real time on a CPU, and needs no account or internet once installed. Kokoro-FastAPI serves it with
OpenAI's speech API:

1. Install uv (already installed here) and optionally espeak-ng.
2. `git clone https://github.com/remsky/Kokoro-FastAPI` somewhere outside this project, then in that folder run
   `.\start-cpu.ps1` (Windows) or `./start-cpu.sh` (Linux/macOS). The first start downloads the model (about
   330 MB). It listens on http://localhost:8880.
3. In `.env`:
   ```
   ANIMATOR_TTS_PROVIDER=openai
   ANIMATOR_TTS_BASE_URL=http://localhost:8880/v1
   ANIMATOR_TTS_MODEL=kokoro
   ANIMATOR_TTS_VOICE=af_heart
   ```
   Other voices: `af_bella`, `af_sky`, `am_michael`, `bf_emma`, `bm_george`.
4. Restart `make dev`. Word timings are estimated for this provider.

Other local options: Piper (very fast, lighter, less natural) and Coqui XTTS-v2 (voice cloning, needs a GPU,
non-commercial licence).

## Adding a provider

A provider is a class with `name`, `voice`, `model`, a `key` property (everything that changes the audio besides
the text), `async synthesize(text) -> Speech` and `async close()`. `Speech` holds the audio bytes, their format
(`mp3`/`wav`), the duration, and optional word marks. Add it to `animator/tts/providers.py`, to `build_provider` in
`animator/tts/__init__.py`, and to the `ANIMATOR_TTS_PROVIDER` choices in `config.py`. Test it over a mocked HTTP
transport (see `apps/api/tests/test_tts.py`). Nothing else changes.

## Files

- `apps/api/src/animator/tts/`: the interface, providers, word timing, audio duration, cache.
- `apps/api/src/animator/pipeline/narration.py`: retiming and the per-scene narration record.
- `apps/api/src/animator/api/tts.py`: serves the audio.
- `apps/api/src/animator/export/narration.py`: the export's narration track.
- `packages/components/src/Caption.tsx`: word-timed captions.
- `apps/web/src/player/voice.ts`: playback in the player.
