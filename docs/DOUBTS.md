# Doubts: raise a hand mid-lesson

A learner watching a lesson can raise a hand at any moment, ask a doubt by typing or speaking, and get
an answer from the tutor: spoken, captioned and shown on screen, built on what the lesson has taught up
to that moment. They can ask again in the same conversation (each answer simpler than the last when it
did not land) and go back to the lesson, which carries on where it stopped. Decisions and measurements:
ADR-050.

## Using it

- **Raise a hand**: the purple hand button at the bottom right of the stage, or the `D` key. The lesson
  pauses and the doubt screen opens over it; the tutor greets the learner (a fixed line, spoken).
- **Ask**: type, or tap the mic and say it (the words appear as they are said and can be corrected
  before sending). Enter sends; Shift+Enter is a new line.
- **The answer** plays on its own stage, with the voice and word-by-word captions:
  - a doubt about something the lesson showed is answered on **the lesson's own picture**, exactly as it
    was when the hand went up: the things the tutor talks about glow, and a short note points at them;
  - a concept the lesson did not draw is answered on **a board**: a title, an icon, an equation when one
    is the answer, and key-point cards written as they are said.
- **After the answer**: "Still not clear" (a simpler answer, from a new angle), "Give an example", or type
  another question; "Show this again" replays an earlier answer. "Got it, continue", "Back to lesson" or
  `Esc` closes the screen and the lesson resumes.
- **A related topic** the lesson does not teach (average velocity in a lesson on average speed) gets a
  short answer and a **Create that lesson** button, which starts that lesson in a new tab.
- **An unrelated question** gets a friendly line and an invitation back to the lesson.

## How long an answer is

The tutor judges how much a doubt needs (quick, standard or deep), within a limit set by the lesson's
length: half the lesson, at least 20 s and at most 60 s (`ANIMATOR_DOUBT_SHARE`, `_MIN_S`, `_MAX_S`). A
one-minute lesson never gets a two-minute answer. A related topic is answered in about 15 s.

## Cost

One model call per question (the tutor's whole answer: what it says, how the voice says it, what the
screen shows), about $0.001-0.002 with `gpt-6-luna`. Nothing else calls a model: the picture is built by
code from the lesson (no scene is written, so nothing needs correcting), the tutor's fixed lines are
spoken once and cached, and the lesson's part of the message is the same for every doubt in a lesson,
so the provider caches it. Speech to text in the browser is free; the server fallback costs about
$0.003 per minute of speech. Limits: `ANIMATOR_DOUBT_BUDGET_USD` per lesson (0.50) and
`ANIMATOR_DOUBT_MAX_TURNS` answers per doubt (8).

## Speaking a doubt

Chrome, Edge and Safari recognise speech themselves (free, words shown live). Other browsers record the
doubt and the server turns it into text with `ANIMATOR_DOUBT_TRANSCRIBE_MODEL` (needs `OPENAI_API_KEY`);
it writes Hindi speech in Devanagari, which the tutor understands as well as Roman Hinglish.

## Settings

| Setting | Default | |
|---|---|---|
| `ANIMATOR_DOUBTS_ENABLED` | true | false: no hand-raise anywhere |
| `ANIMATOR_MODEL_DOUBT` | (empty) | empty: the teacher's model, then `ANIMATOR_MODEL_FALLBACKS` |
| `ANIMATOR_EFFORT_DOUBT` | low | reasoning effort of the answer |
| `ANIMATOR_DOUBT_SHARE`, `_MIN_S`, `_MAX_S` | 0.5, 20, 60 | the longest answer |
| `ANIMATOR_DOUBT_BUDGET_USD` | 0.5 | all doubts of one lesson |
| `ANIMATOR_DOUBT_MAX_TURNS` | 8 | answers in one doubt |
| `ANIMATOR_DOUBT_TRANSCRIBE_MODEL` | gpt-4o-mini-transcribe | server speech to text |

## Where things are

- API: `apps/api/src/animator/doubts/` (`service.py` the flow, `context.py` what the tutor is told,
  `visuals.py` the picture, `phrases.py` the fixed lines, `prompts/doubt.v1.md`, `router.py` the routes
  under `/api/doubts`).
- Web: `apps/web/src/doubts/` (the hand, the doubt screen, the answer player, speech input). The lesson
  player only offers an optional `addon` slot.
- Doubts are saved next to their lesson, `var/generations/<lesson>/doubts/<doubt>.json` (questions,
  answers, scenes, cost, timing). A lesson's own record, call log and cost are never changed.
