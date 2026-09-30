"""Lesson languages offered in the app (ADR-038, ADR-039, ADR-041). The language is the one the explainer
SPEAKS: it applies to the narration (the voice and the captions) only. Everything written in the visuals
stays English. The model is told the language in one line of each stage's message; English keeps that
line exactly as before ("Language: English")."""

from __future__ import annotations

LANGUAGES = ("English", "Hinglish")

HINGLISH = (
    "Hinglish for the narration only. The narration (the sentences the voice says, also shown as the "
    "captions) is in Hinglish, written only in Roman (English) letters, never Devanagari. Write it the way a "
    "warm, friendly Indian teacher actually talks to a student: natural spoken Hinglish with Hindi sentence "
    "flow and every technical or science term in English; short sentences (about 8 to 16 words); everyday "
    "words like dekho, socho, chalo, achha, yaani, matlab, bilkul; questions to the learner that sound like "
    "questions ('Socho, agar hum ball ko upar phenkein, toh kya hoga?'); encouragement ('Bilkul sahi!', "
    "'Bahut badhiya!'); and the learner addressed warmly and consistently (tum for children and teens, aap "
    "for adults). Punctuate for speaking: a comma wherever a teacher pauses, a question mark on every "
    "question, an exclamation mark for surprise or praise, a full stop between thoughts; never ';' or ':' "
    "in narration. Use the common Roman spellings Indians write (hai, nahi, kyun, achha, yeh, woh, "
    "samjhe). EVERYTHING ELSE IS IN ENGLISH, exactly as in an English lesson: the lesson and scene titles, "
    "every text drawn on screen (titles, labels, cards, notes, badges, speech bubbles, readouts, "
    "equations), the descriptions of what happens on screen, and checkpoint questions, choices and "
    "explanations"
)


def language_line(language: str) -> str:
    """How a stage's message names the lesson language."""
    return HINGLISH if language.strip().lower() == "hinglish" else language
