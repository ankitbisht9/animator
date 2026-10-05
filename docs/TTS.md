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

1. **While a scene is being written**, the pipeline already synthesizes the storyboard's sentences. The model
   copies those sentences into the scene, so the speech is usually ready before the scene is. Measured cost of
   the voice step: 0.02-0.07 s per scene.
2. **One take per scene (ADR-043, `ANIMATOR_TTS_ONE_TAKE=1`).** The voice reads the whole scene's narration in
   one recording. It flows from one sentence into the next, with only the pauses its own commas and full stops
   make; nothing restarts between sentences.
   - Each beat plays its part of that recording. Parts are cut in the middle of the pause between two sentences.
   - The next scene follows after `ANIMATOR_TTS_SCENE_PAUSE_S` (1.0 s) of quiet.
   - Recordings are cached by content: the same narration in the same voice is synthesized only once, across
     drafts, scenes and lessons. Files go to `var/tts/<id>.mp3`, plus `<id>.json` holding the duration and word
     timings.
   - `ANIMATOR_TTS_ONE_TAKE=0` speaks each sentence on its own (ADR-041). The voice then starts afresh at each
     sentence, with a pause of `ANIMATOR_TTS_PAUSE_S` (0.35 s) between them.
3. **Fitting the scene to the voice (ADR-041).** In one take, each beat lasts exactly its part of the recording,
   and the last one until its last word plus the pause before the next scene. Sentence by sentence, each beat
   lasts until its last word is said, then 0.35 s (the scene's last beat rests 0.6 s). Either way the time is
   measured to the last word, not to the end of the audio file: voices add up to 0.9 s of silence after the
   last word.
   - A beat whose speech is longer is stretched; a beat with a long silence after its sentence is shortened, but
     never below what its own actions need (an action starting at `beat:b2` with a 2 s delay keeps its 2 s).
   - Times inside a beat move proportionally; later beats and the scene end move with it. Beat references,
     `after`/`with` chains, delays and motion durations are unchanged.
   - Three timings are tried in order: fitted, halfway (half the extra silence kept), and stretch-only. The
     first one that the engine finds valid with no more visual glitches than before is kept; otherwise the
     scene keeps the model's timing. `ANIMATOR_TTS_FIT_BEATS=0` tries stretch-only, as before ADR-041.
4. **Word timings.** The provider's word marks are matched to the caption's words by their letters and digits.
   A mark that covers several words ("9.8 m/s²" said as one unit) is shared between them. Words said differently
   ("twelve" for "12"), and every word from providers without marks, are estimated from word length and
   punctuation pauses within the real audio duration.
5. **Player.** The beat on screen plays its audio from the player's clock, from the beat's `offset` into it. In one
   take, consecutive beats continue the same audio, so it plays through without a restart. Pause, seek, speed
   (0.5x-2x), checkpoints and "preparing the next scene" all follow that clock, and the audio is re-synced if it
   drifts by more than 0.25 s. The speaker button (or the `m` key) turns the voice off and on for the viewer.
6. **Export.** Each beat's part of its audio is placed at its time in the lesson, muxed as AAC, and padded to the
   exact video length. The captions in the video follow the voice word by word.
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
| `kokoro` | free, local, offline | none | exact | Kokoro-82M on your own Kokoro-FastAPI server (see below). About real-time speed on a laptop CPU. |
| `openai` | OpenAI: paid (about $0.015 per minute); local server: free | `OPENAI_API_KEY` (already set) or none | estimated | Any server with OpenAI's `/audio/speech` API. |

Voice and speed: `ANIMATOR_TTS_VOICE` (English lessons; empty = the provider's default),
`ANIMATOR_TTS_VOICE_HINGLISH` (Hinglish lessons, see below) and `ANIMATOR_TTS_SPEED` (1.0 = normal).
Write each setting once in `.env`: when a name appears twice, only the last one counts. The API log and the home
page warn about that.
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
   - `ANIMATOR_TTS_MODEL` picks the model:
     - `eleven_multilingual_v2` (the default): the most natural and even delivery.
     - `eleven_flash_v2_5`: fastest (about 75 ms), half a credit per character, so it doubles the quota.
       Slightly flatter.
     - `eleven_v3`: the most expressive. It gets the sentence's tone as an audio tag (`[curious]`,
       `[encouraging]`, ...). It is slower, and its stability is only 0, 0.5 or 1.
   - `ANIMATOR_TTS_STABILITY` (0.4) and `ANIMATOR_TTS_STYLE` (0.3) set the delivery: lower stability and higher
     style are more emotional and less even.
   - `ANIMATOR_TTS_CONCURRENCY`: ElevenLabs allows 2 requests at once on the free plan, 3 on Starter, 5 on
     Creator.
5. Restart `make dev`. A 1-minute lesson uses about 700-900 characters.

### OpenAI TTS (paid, no new key)

`ANIMATOR_TTS_PROVIDER=openai`. It uses `OPENAI_API_KEY`, model `gpt-4o-mini-tts` and voice `coral`. Tone of voice
is set with `ANIMATOR_TTS_INSTRUCTIONS`, for example "Speak like a warm, enthusiastic teacher."

## Free local voice: Kokoro-82M

Kokoro-82M is an open-weight model (Apache-2.0) with 82M parameters. It sounds close to the best cloud voices and
needs no account or internet once installed. Kokoro-FastAPI serves it; the `kokoro` provider uses its captioned
endpoint, which also returns when every word is said (exact caption sync).

1. Install uv (already installed here) and optionally espeak-ng.
2. `git clone https://github.com/remsky/Kokoro-FastAPI` **outside this project** (for example `D:\Kokoro-FastAPI`),
   then in that folder run `.\start-cpu.ps1` (Windows) or `./start-cpu.sh` (Linux/macOS). The first start
   downloads the model (about 330 MB). It listens on http://localhost:8880.
3. In `.env`:
   ```
   ANIMATOR_TTS_PROVIDER=kokoro
   ANIMATOR_TTS_BASE_URL=http://localhost:8880
   ANIMATOR_TTS_VOICE=af_heart
   ANIMATOR_TTS_PROVIDER_HINGLISH=edge
   ANIMATOR_TTS_VOICE_HINGLISH=hi-IN-SwaraNeural
   ANIMATOR_TTS_CONCURRENCY=1
   ANIMATOR_TTS_TIMEOUT_S=60
   ```
4. Restart `make dev`, with Kokoro running.

**Speed.** Without an NVIDIA GPU, Kokoro speaks at about real time: a 5-second sentence takes about 5 seconds, and
several at once are no faster. With `ANIMATOR_TTS_CONCURRENCY=1`, the sentences are spoken in scene order, so the
first scene's voice is ready first and the rest stays ahead of playback. The whole lesson finishes later than with
`edge` (see ADR-038 for measurements). With an NVIDIA GPU, `.\start-gpu.ps1` is many times faster.

**Voices.** 72 voices; the name says the language and gender: `af_` American English female, `am_` American
male, `bf_`/`bm_` British, `hf_`/`hm_` Hindi, and also Spanish (`e`), French (`f`), Italian (`i`), Japanese
(`j`), Brazilian Portuguese (`p`) and Mandarin (`z`). Kokoro's own quality grades: `af_heart` A, `af_bella` A-,
`af_nicole` and `bf_emma` B-; `am_michael`, `am_fenrir`, `am_puck` C+ (the best male voices); the Hindi voices C.
Voices can be blended: `af_bella+af_sky`, or weighted `af_bella(2)+af_sky(1)`.

**Kokoro cannot speak Roman-script Hinglish with an Indian accent**, not even in its Hindi voices: its Hindi
pronunciation rules expect Devanagari, and Roman letters are read with English sounds ("neeche" comes out as
English "neech", "hai" as "hi"). So Hinglish lessons go to edge (`ANIMATOR_TTS_PROVIDER_HINGLISH=edge`), whose
Indian voices read Roman Hinglish natively, while English lessons stay on Kokoro.

## Choosing a voice by listening

- **Kokoro's own page**: http://localhost:8880/web/ while Kokoro runs. Type any text, pick a voice, play it.
- **Samples of this project's sentences**: `uv run python scripts/tts_samples.py` speaks one English and one
  Hinglish sentence in each recommended voice of the provider set in `.env` (`--provider edge` for another,
  `--all` for every voice) and writes `var/voice-samples/<provider>/index.html`. Open it and click play; each row
  shows the `.env` line for that voice.

## Spoken language: English or Hinglish

The create form has a **Spoken in** choice: the language the explainer speaks.
- **English**: unchanged; the model receives exactly the same instructions as before.
- **Hinglish**: only the narration changes. The voice, and the captions that show what it says, are in the everyday
  Hindi-English mix, written in Roman letters, with technical terms in English ("Socho, agar hum ball ko upar
  phenkein, toh gravity use wapas neeche kheenchti hai.").
- **Everything drawn in the visuals stays English** in both languages: lesson and scene titles, labels, cards,
  badges, speech bubbles, readouts, equations and checkpoint questions (ADR-039).

A Hinglish lesson speaks through `ANIMATOR_TTS_PROVIDER_HINGLISH` (empty = the main provider) with
`ANIMATOR_TTS_VOICE_HINGLISH` (empty = that provider's Hinglish default):

| Provider | Hinglish voice | |
|---|---|---|
| `edge`, `azure` | `hi-IN-SwaraNeural` (female, native Hindi) | recommended; also `en-IN-NeerjaNeural` and `en-IN-NeerjaExpressiveNeural` (female, Indian English) |
| `google` | `en-IN-Neural2-A` (female, Indian English) | |
| `kokoro` | `hf_alpha` (female) | English-sounding: see the Kokoro section, and use edge for Hinglish |
| `elevenlabs`, `openai` | the English voice | their multilingual models speak Hindi |

Swara was chosen by a listening test (ADR-039). The same Hinglish lines were spoken by every female candidate, and
an audio model rated gender, accent and naturalness. Swara scored highest: Hindi 9-10, English terms 8-9,
naturalness 9. Kokoro's Hindi voices were heard as male or English-sounding.

## The teacher's voice (ADR-041)

**How the Hinglish is written.** The Hinglish is written directly by the lesson's own model calls (outline and
storyboard), in Roman letters. It is not written in English and then translated. The language line those calls
receive is in `pipeline/language.py`. (ADR-041 had lengthened it into a teacher-style brief; ADR-042 reverted it
to the ADR-039 line, because the scene-drawing model receives it too.)

**The speech script.** The captions stay in Roman letters, but that is not what the voice reads. For Hinglish
lessons (`ANIMATOR_TTS_SCRIPT=hinglish`), one small model call per scene (the `lightweight` stage, about $0.0004,
run while the scene is drawn) rewrites each caption for the voice:
- **Hindi words in Devanagari.** Voices read Roman Hindi with English spelling rules: "sunati" was said as
  "sunti", and "ghumati" as "ghumti". Written as सुनाती and घुमाती, they are pronounced right by every provider.
- **English terms stay English**: hypotenuse, square, force.
- **Spoken punctuation.** `:` and `;` become commas (voices read them as full stops), and commas go where a
  teacher pauses.
- **A tone for the sentence**: curious, encouraging, excited, thoughtful, warm, serious or neutral. ElevenLabs
  `eleven_v3` receives it as an audio tag. The other voices hear the tone through the punctuation.

It returns exactly one spoken entry per caption word, so each caption word still lights up when it is said. The
scripts are cached in `var/tts/scripts/`. If the call fails, or a reply does not line up with the caption's words,
that caption is read as written. `ANIMATOR_TTS_SCRIPT=all` scripts English lessons too (for tone and
punctuation); `off` turns it off. Without a script, `:` and `;` are still read as commas.

**Flow between sentences.** In one take (the default), the scene is a single recording, so the voice never
restarts between sentences. A scene's script is its sentences' scripts one after another, and `eleven_v3` gets the
first sentence's tone. Sentence by sentence, ElevenLabs (except `eleven_v3`) gets the previous and the next
sentence with each request, so the intonation carries across.

**Which ElevenLabs model (ADR-044).** The same Hinglish teaching paragraph was spoken with the same voice by
`eleven_flash_v2_5`, `eleven_multilingual_v2` and `eleven_v3`, and an audio model rated each twice as a teacher's
delivery. Flash scored as well as the others or better (naturalness 8-9, pacing 9, Hindi 9, flow 8-9), so it stays.
Delivery now comes mostly from the script: the teaching pipeline's teacher writes complete conversational sentences
for the ear, with questions, signposts and pauses.

Measured on live lessons (ElevenLabs `eleven_flash_v2_5`):
- Sentence by sentence (ADR-041): the silence between sentences went from about 1.3 s to 0.35 s, but each
  sentence still started afresh.
- One take (ADR-043): one recording per scene. Between sentences you hear only the voice's own pause at the full
  stop (0.0-0.95 s, 0.4 s on average), and about 1.0-1.1 s between scenes.
- The speech script adds 3-5 calls and about $0.001 to a lesson, with no waiting: it runs while the scenes are
  drawn.

## Voice modulation (ADR-045)

`ANIMATOR_TTS_PROSODY=1` (default) makes the tutor sound like a teacher who knows what matters in what they say.
- **Direction.** The speech script also marks, for each sentence, its tone and pace (slow for definitions,
  equations and calculations), up to two stressed words, and a pause before a key point. It sees what the
  picture shows while each sentence is said.
- **What the model gets.** Eleven v3 and v4 get a delivery tag where the tone changes (`[curious]`,
  `[excited]`, `[thoughtfully]`, `[seriously]`, ...) and the stressed English words in capitals. Other models
  get the words as they are; they would read tags aloud.
- **Timing on the audio, for every model.**
  - At least `ANIMATOR_TTS_SENTENCE_PAUSE_S` (0.4 s) after a full stop, `ANIMATOR_TTS_COMMA_PAUSE_S` (0.18 s)
    after a comma and `ANIMATOR_TTS_KEY_PAUSE_S` (0.35 s) before a key point. Voices sometimes run two
    sentences together; this fixes that.
  - Slow sentences at `ANIMATOR_TTS_SLOW_TEMPO` (0.9, pitch unchanged).
  - The captions' word times move with it.
  - The audio saved is the shaped audio, the one the word times were measured on, so each caption
    word lights up as it is said (ADR-048). Lessons made before then play the unshaped audio, which
    runs ahead of their captions: regenerate them.
  - Needs ffmpeg (`FFMPEG_PATH`).
- **Voice and picture.** A highlight lands when the thing it highlights is named
  (`ANIMATOR_TTS_SYNC_HIGHLIGHTS`).

**Which ElevenLabs model.** `eleven_v4` is recommended.
- It speaks about 130 words a minute (flash: about 200) with about 60% more pitch variation.
- An audio judge preferred it with modulation over flash without, 12 to 4.
- It costs about 1 credit per character after its launch discount (until 12 October 2026); flash costs 0.5.
- `eleven_flash_v2_5` with modulation keeps the guaranteed pauses at half the credits.
- Measurements are in ADR-045.

## No voice?

- The lesson page says "Voice: N scene(s) have captions only" with the reason, and the home page shows a
  configuration problem (a missing key, a duplicated setting) before you create a lesson.
- `ANIMATOR_TTS_ENABLED=1`, and the API was restarted after changing `.env`.
- With `kokoro`, the Kokoro server must be running (http://localhost:8880/web/ opens).
- Lessons made while the voice was failing keep captions only; create the lesson again.

## Adding a provider

A provider is a class with `name`, `voice` (its default voice), `hinglish_voice`, `model`, a `cache_key(voice)`
method (everything that changes the audio besides the text), `async synthesize(text, voice=None, context=None)
-> Speech` and `async close()`. `context` (a `SpeechContext`: the previous and next sentence and the tone) may be
ignored. `Speech` holds the audio bytes, their format
(`mp3`/`wav`), the duration, and optional word marks. Add it to `animator/tts/providers.py`, to `build_provider` in
`animator/tts/__init__.py`, and to the `ANIMATOR_TTS_PROVIDER` choices in `config.py`. Test it over a mocked HTTP
transport (see `apps/api/tests/test_tts.py`). Nothing else changes.

## Files

- `apps/api/src/animator/tts/`: the interface, providers, word timing, audio duration, cache.
- `apps/api/src/animator/tts/script.py` and `speech_script.v1.md`: the speech script and its prompt.
- `apps/api/src/animator/pipeline/narration.py`: fitting beats to speech and the per-scene narration record.
- `apps/api/src/animator/pipeline/language.py`: how the lesson's calls are told to write Hinglish.
- `apps/api/src/animator/api/tts.py`: serves the audio.
- `apps/api/src/animator/export/narration.py`: the export's narration track.
- `packages/components/src/Caption.tsx`: word-timed captions.
- `apps/web/src/player/voice.ts`: playback in the player.
