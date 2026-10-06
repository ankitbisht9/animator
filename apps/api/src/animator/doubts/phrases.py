"""What the tutor says around a doubt (ADR-050), from a fixed set so it costs no model call: a greeting
when a hand goes up, a line while the answer is being prepared, an invitation after it, and a line on
going back to the lesson. Each is spoken once and then served from the speech cache.

Hinglish lines are shown in Roman letters and spoken from Devanagari (as the lesson's narration is,
ADR-041); they avoid gendered verb forms, whoever the voice is."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Phrase:
    text: str  # what is shown
    spoken: str  # what the voice reads


def _en(*lines: str) -> tuple[Phrase, ...]:
    return tuple(Phrase(x, x) for x in lines)


PHRASES: dict[str, dict[str, tuple[Phrase, ...]]] = {
    "english": {
        "greeting": _en(
            "Oh, a hand up! What's on your mind?",
            "Sounds like a doubt! Go ahead, ask me anything.",
            "Yes? What would you like to know?",
            "I love questions. What's confusing you?",
            "Let's clear that up right now. What's your doubt?",
        ),
        "thinking": _en(
            "Good question. Let me show you.",
            "Ah, I see what you mean. Look at this.",
            "Great question! Here's how to think about it.",
            "Okay, let's work through it together.",
        ),
        "followup": _en(
            "Does that help? Ask me more, or we can carry on.",
            "Clear now? If not, just ask again.",
        ),
        "resume": _en(
            "Great, let's get back to the lesson!",
            "Awesome. On with the lesson!",
        ),
    },
    "hinglish": {
        "greeting": (
            Phrase("Arre, haath upar! Batao, kya doubt hai?", "अरे, हाथ ऊपर! बताओ, क्या doubt है?"),
            Phrase(
                "Lagta hai koi doubt hai! Poochho, bilkul befikar hoke.",
                "लगता है कोई doubt है! पूछो, बिल्कुल बेफ़िक्र होके।",
            ),
            Phrase("Haan, batao, kya samajh nahi aaya?", "हाँ, बताओ, क्या समझ नहीं आया?"),
            Phrase(
                "Sawaal poochhna achhi baat hai! Kya confuse kar raha hai?",
                "सवाल पूछना अच्छी बात है! क्या confuse कर रहा है?",
            ),
            Phrase(
                "Ruko, pehle yeh clear karte hain. Kya doubt hai?", "रुको, पहले यह clear करते हैं। क्या doubt है?"
            ),
        ),
        "thinking": (
            Phrase("Achha sawaal hai! Chalo, dekhte hain.", "अच्छा सवाल है! चलो, देखते हैं।"),
            Phrase("Theek hai, ise milkar samajhte hain.", "ठीक है, इसे मिलकर समझते हैं।"),
            Phrase("Bahut badhiya sawaal! Aise socho.", "बहुत बढ़िया सवाल! ऐसे सोचो।"),
            Phrase("Chalo, isko step by step dekhte hain.", "चलो, इसको step by step देखते हैं।"),
        ),
        "followup": (
            Phrase("Ab clear hua? Nahi toh phir se poochho.", "अब clear हुआ? नहीं तो फिर से पूछो।"),
            Phrase("Samajh aaya? Aur kuch poochhna ho toh batao.", "समझ आया? और कुछ पूछना हो तो बताओ।"),
        ),
        "resume": (
            Phrase("Badhiya! Chalo, lesson pe wapas chalte hain.", "बढ़िया! चलो, lesson पे वापस चलते हैं।"),
            Phrase("Shabaash! Ab aage badhte hain.", "शाबाश! अब आगे बढ़ते हैं।"),
        ),
    },
}


def phrases_for(language: str) -> dict[str, tuple[Phrase, ...]]:
    return PHRASES.get(language.strip().lower(), PHRASES["english"])
