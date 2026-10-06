"""What the tutor answers a raised hand with (ADR-050): one structured reply from one model call. The
model never writes scene DSL here; it says what to say and what to point at, and code builds the
picture (doubts/visuals.py)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Tone = Literal["neutral", "warm", "curious", "excited", "encouraging", "thoughtful", "serious"]
Kind = Literal["lesson", "related", "unrelated", "unclear"]
Depth = Literal["quick", "standard", "deep"]


class EquationPart(BaseModel):
    id: str = Field(description="A short id for this part of the equation, e.g. 'v', 'eq', 'd'")
    tex: str = Field(description="This part in TeX, e.g. 'v_{avg}', '=', '\\frac{d}{t}'")


class DoubtBeat(BaseModel):
    text: str = Field(
        description="One spoken sentence, also shown as the caption. Hinglish lessons: Roman letters only. "
        "**double asterisks** around the one key term may be used."
    )
    spoken: list[str] | None = Field(
        description="Hinglish lessons only: how the voice says `text`, EXACTLY one entry per word of `text` "
        "(split on spaces), same order: Hindi words in Devanagari, English words as written. Null in "
        "English lessons."
    )
    tone: Tone = Field(description="How this sentence is said")
    focus: list[str] = Field(
        description="Picture answers: ids (from 'On screen') of at most two things this sentence talks "
        "about; they are highlighted while it is said. Empty for a board answer or a sentence about "
        "nothing on screen."
    )
    note: str | None = Field(
        description="At most 7 words shown on screen while this sentence is said: in a picture answer a "
        "pointer note at focus[0]; on a board a key-point card. English only. Null for most sentences."
    )


class DoubtVisual(BaseModel):
    mode: Literal["scene", "board"] = Field(
        description="scene: reuse a lesson scene's picture (the doubt is about something shown); board: "
        "a clean board with key-point cards (a concept, a definition, something not shown)"
    )
    scene: int | None = Field(
        description="scene mode: the NUMBER of the lesson scene whose picture is used (usually the "
        "current one); null for a board"
    )
    icon: str | None = Field(description="board: one icon name from the list that fits the answer; else null")
    equation: list[EquationPart] | None = Field(
        description="board: an equation that answers the doubt, split into parts; null when none helps"
    )
    equation_beat: int | None = Field(
        description="The number of the sentence (1-based) while which the equation is written; null "
        "without an equation"
    )


class DoubtAnswer(BaseModel):
    kind: Kind = Field(
        description="lesson: about what this lesson teaches or showed; related: a nearby topic this "
        "lesson does not teach; unrelated: nothing to do with the lesson's subject; unclear: the "
        "question cannot be understood"
    )
    depth: Depth = Field(description="quick, standard or deep: how much explaining this doubt needs")
    title: str = Field(description="A short title for the answer, at most 6 words, English")
    visual: DoubtVisual
    beats: list[DoubtBeat] = Field(min_length=1, max_length=10)
    suggestion: str | None = Field(
        description="related only: the topic a new lesson could teach, a few words in English; else null"
    )


class AskRequest(BaseModel):
    """A question raised at a moment of a lesson (a new doubt, or a follow-up in the same one)."""

    question: str = Field(min_length=1, max_length=1000)
    scene_index: int = Field(ge=0)
    t: float = Field(ge=0.0, description="Seconds into that scene when the hand was raised")
    session_id: str | None = Field(default=None, pattern=r"^d_[0-9a-f]{12}$")
