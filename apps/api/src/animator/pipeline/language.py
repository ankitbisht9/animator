"""Lesson languages offered in the app (ADR-038, ADR-039). The language is the one the explainer SPEAKS:
it applies to the narration (the voice and the captions) only. Everything written in the visuals stays
English. The model is told the language in one line of each stage's message; English keeps that line
exactly as before ("Language: English")."""

from __future__ import annotations

LANGUAGES = ("English", "Hinglish")

HINGLISH = (
    "Hinglish for the narration only. The narration (the sentences the voice says, also shown as the "
    "captions) is in Hinglish: the everyday mix of Hindi and English that people in India speak, written "
    "only in Roman (English) letters, never Devanagari, with every technical or science term in English, "
    "for example: 'Socho, agar hum ball ko upar phenkein, toh gravity use wapas neeche kheenchti hai.' "
    "EVERYTHING ELSE IS IN ENGLISH, exactly as in an English lesson: the lesson and scene titles, every "
    "text drawn on screen (titles, labels, cards, notes, badges, speech bubbles, readouts, equations), the "
    "descriptions of what happens on screen, and checkpoint questions, choices and explanations"
)


def language_line(language: str) -> str:
    """How a stage's message names the lesson language."""
    return HINGLISH if language.strip().lower() == "hinglish" else language
