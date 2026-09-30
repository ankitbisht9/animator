"""Lesson languages offered in the app (ADR-038). The model is told the language in one line of
each stage's message; English keeps that line exactly as before ("Language: English")."""

from __future__ import annotations

LANGUAGES = ("English", "Hinglish")

HINGLISH = (
    "Hinglish: the everyday mix of Hindi and English that people in India speak, written only in Roman "
    "(English) letters, never in Devanagari (the fonts cannot draw it). Hindi sentence flow with common "
    "English words, and every technical or science term in English, for example: 'Socho, agar hum ball ko "
    "upar phenkein, toh gravity use wapas neeche kheenchti hai.' Narration, questions and checkpoint choices "
    "are in Hinglish; on-screen titles and labels are short, in simple English or Roman-script Hinglish"
)


def language_line(language: str) -> str:
    """How a stage's message names the lesson language."""
    return HINGLISH if language.strip().lower() == "hinglish" else language
