# Model trials

An admin can make a lesson with another AI model, through OpenRouter, and compare it with the normal
setup (gpt-6-luna through OpenAI). This is for testing only. A lesson that does not pick a trial model is
made exactly as before, and a trial changes nothing for anyone else. Decisions and findings: ADR-056.

## Setting it up

In `.env` (git-ignored, never committed):

```
OPENROUTER_API_KEY=sk-or-...
ANIMATOR_TRIAL_MODELS=qwen/qwen3.5-flash-02-23,deepseek/deepseek-v4-pro,qwen/qwen3.5-9b
```

Then restart the API (stop `make dev` and start it again). Any OpenRouter model id can go in the list.

**The OpenRouter account needs credits.** OpenRouter refuses a call that could take the balance below
zero, and it counts every call in flight at its longest possible answer. A lesson runs several long calls
at once, so keep a few dollars there; about $5 covers a set of test lessons. A lesson that runs short
stops as *paused* ("OpenRouter credits are used up"). Add credits, then press Resume: it carries on with
the same model.

## Comparing models

1. **Create → Advanced options → AI model (testing)** (admins only). "Normal (gpt-6-luna)" is the usual
   setup; the others are the trial models.
2. Make the same topic once with each model: same length, level, lesson type and narration language.
3. Compare:
   - **The lessons.** A trial lesson carries a flask badge with its model's name on its page, in My
     lessons, in All lessons and in Credits & cost.
   - **Administration → All lessons → AI cost**: what the AI model calls cost to make the lesson. For a
     trial lesson this is OpenRouter's own figure for each call, so it is the column to compare models by.
   - **Administration → Credits & cost**: everything the lesson cost in the period shown:
     *Making* = AI plus the ElevenLabs voice (an estimate, $200 per 1M characters by default,
     `PLATFORM_VOICE_PRICES`); *Doubts* = answers to doubts asked on it (AI, voice, speech to text). The
     voice is the same whichever model wrote the lesson, so it is why this total is higher than AI cost.
   - **The pipeline view** (the ↗ button in All lessons): every call with its stage, model, tokens, time
     and cost.

From the command line, the same topics with a report (voice off, so only AI is spent):

```
uv run python -m animator.eval --name flash --trial-model qwen/qwen3.5-flash-02-23 \
    --only heart --duration-min 1 --set ANIMATOR_TTS_ENABLED=false
```

The report is `var/eval/<name>/report.md`. Without `--trial-model` it makes the lessons with the normal
models, as a baseline.

## What a trial lesson does

- **Every call of that lesson goes to the chosen model**: teacher, reviewer, visual director, scenes,
  corrections, critics, the voice's script, and the doubts asked in it later. Other lessons, at the same
  time, use the normal models.
- **Pictures:** the step that looks at the drawn scenes (the visual critic) stays on the normal model when
  the trial model cannot read images. DeepSeek V4 Pro reads text only.
- **Reasoning:** each stage's effort, passed as OpenRouter's `reasoning.effort`.
- **Answers in the schema:** every step answers in a JSON schema. OpenRouter is asked to use only providers
  that support that. Some accept the schema and do not apply it. Qwen3.5-Flash's only provider, Alibaba,
  answers JSON of its own, and fails to keep to JSON at all while the model reasons. The client notices
  and steps that model up for the rest of the run: first the schema is written into its instructions;
  if that is not enough, reasoning is turned off too. The log says so ("did not keep to the JSON
  schema"). Each step-up costs one extra call. A scene's schema is about 30k tokens, so only a model that
  needs it gets it in the prompt.
- **Cost:** what OpenRouter reports for each call (it depends on which provider served it); when it
  reports nothing, the model's listed price.
- **The voice** (ElevenLabs) is the same as for any other lesson.

## Ending the trial, or keeping a model

- **End:** empty `ANIMATOR_TRIAL_MODELS` (or delete the lines) and restart. Trial lessons stay playable
  and keep their badge. The picker disappears.
- **Keep a model:** the normal models are chosen per stage in `.env` (`ANIMATOR_MODEL_*`) and are called
  through OpenAI. Making an OpenRouter model the normal one is a separate change; decide on the model
  first.

## Security

The key lives only in `.env`. It is never logged, never sent to the browser and never stored with a
lesson. Which lessons were trials, and with which model, is kept in `var/model_trials.json`.
