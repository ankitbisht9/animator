"""Which saved drawings a scene may use (ADR-055): only the ones its brief names.

A drawing is offered to a scene only when the scene's brief names exactly that thing:
- one of its names (`aliases`) appears as a whole word or phrase (plurals included: "leaves" names
  a leaf, "nuclei" a nucleus), within one sentence;
- and no phrase in the scene or the lesson's title means something else (`not_for`: "heart shape",
  "computer virus"): then the drawing is not offered at all, whatever else the scene says;
- and the lesson is about that kind of thing: one of its `context` stems appears in the scene or the
  lesson's topic ("blood", "pump" for a heart; never a heart in a lesson about feelings).
When two drawings are named by overlapping words, the longer name wins ("plant cell" names the plant
cell, not a cell). Similar is not enough: a drawing of something else is never offered, so a scene
draws what the library does not have exactly.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from animator.objects.models import SavedObject

_TOKEN = re.compile(r"[a-z0-9]+")
_SENTENCE = re.compile(r"[.!?;:\n]+")

# Plurals that do not end in s (science names first).
IRREGULAR = {
    "nuclei": "nucleus",
    "mitochondria": "mitochondrion",
    "bacteria": "bacterium",
    "fungi": "fungus",
    "alveoli": "alveolus",
    "bronchi": "bronchus",
    "bronchioles": "bronchiole",
    "stomata": "stoma",
    "cacti": "cactus",
    "algae": "alga",
    "larvae": "larva",
    "vertebrae": "vertebra",
    "criteria": "criterion",
    "phenomena": "phenomenon",
    "data": "datum",
    "men": "man",
    "women": "woman",
    "children": "child",
    "people": "person",
    "teeth": "tooth",
    "feet": "foot",
    "geese": "goose",
    "mice": "mouse",
    "lice": "louse",
    "oxen": "ox",
    "dice": "die",
}


def forms(word: str) -> frozenset[str]:
    """The word and the singular forms it may stand for ("leaves": leaf, leave; "cells": cell)."""
    out = {word}
    if word in IRREGULAR:
        out.add(IRREGULAR[word])
    if len(word) > 3 and word.endswith("ies"):
        out.add(word[:-3] + "y")
    if len(word) > 3 and word.endswith("ves"):
        out.update({word[:-3] + "f", word[:-3] + "fe"})
    if len(word) > 3 and word.endswith("es"):
        out.add(word[:-2])
    if len(word) > 2 and word.endswith("s") and not word.endswith("ss"):
        out.add(word[:-1])
    return frozenset(out)


def segments(text: str) -> list[list[str]]:
    """Text as sentences of lowercase word tokens (underscores split words: `red_blood_cell`)."""
    out: list[list[str]] = []
    for part in _SENTENCE.split(text.lower().replace("_", " ")):
        toks = _TOKEN.findall(part)
        if toks:
            out.append(toks)
    return out


def _phrase(text: str) -> list[frozenset[str]]:
    return [forms(t) for t in _TOKEN.findall(text.lower().replace("_", " "))]


def _occurrences(phrase: list[frozenset[str]], sentence: Sequence[str]) -> list[tuple[int, int]]:
    n = len(phrase)
    if n == 0 or n > len(sentence):
        return []
    out = []
    for i in range(len(sentence) - n + 1):
        if all(phrase[k] & forms(sentence[i + k]) for k in range(n)):
            out.append((i, i + n))
    return out


def _stem_in(stem: str, sentences: Iterable[Sequence[str]]) -> bool:
    """A context entry: one word stem ("circulat" matches "circulation"), or a phrase."""
    words = _TOKEN.findall(stem.lower())
    if not words:
        return False
    for s in sentences:
        for i in range(len(s) - len(words) + 1):
            if all(s[i + k] == w for k, w in enumerate(words[:-1])) and s[i + len(words) - 1].startswith(
                words[-1]
            ):
                return True
    return False


@dataclass
class Mention:
    obj: SavedObject
    where: int  # which text (0: the brief's objects, then the rest)
    sentence: int
    span: tuple[int, int]
    alias: str


def _masked(texts: list[list[list[str]]], found: list[Mention]) -> list[list[str]]:
    """Every sentence, with the words of these mentions blanked out."""
    out: list[list[str]] = []
    for w, sentences in enumerate(texts):
        for si, sentence in enumerate(sentences):
            words = list(sentence)
            for m in found:
                if m.where == w and m.sentence == si:
                    for k in range(*m.span):
                        words[k] = ""
            out.append(words)
    return out


@dataclass
class Offer:
    obj: SavedObject
    # The brief's objects ("id: description") that name it, by id.
    for_ids: list[str] = field(default_factory=list)
    mentions: int = 0
    preferred: int = 0


@dataclass
class SceneText:
    """What a scene's brief says, split for matching: its objects ("id: description") one by one,
    the rest of the brief, and the lesson (for context words only)."""

    objects: list[str]
    body: list[str]
    lesson: list[str] = field(default_factory=list)


def _mentions(obj: SavedObject, texts: list[list[list[str]]]) -> list[Mention]:
    aliases = [(a, _phrase(a)) for a in obj.aliases]
    out: list[Mention] = []
    for w, sentences in enumerate(texts):
        for si, sentence in enumerate(sentences):
            for alias, phrase in aliases:
                out.extend(Mention(obj, w, si, sp, alias) for sp in _occurrences(phrase, sentence))
    return out


def _meant_otherwise(
    obj: SavedObject, texts: list[list[list[str]]], found: list[Mention], lesson: list[list[str]]
) -> bool:
    """Does the scene or lesson say one of the phrases that mean something else ("computer virus",
    "heart shape")? Then the drawing is not offered at all: when in doubt, the scene draws it. A phrase
    inside one of the drawing's own names where that name is said ("candle" in "red comparison candle")
    does not count; one that extends the name ("heart shape" over "heart") does."""
    blocked = [_phrase(p) for p in obj.not_for]
    if not blocked:
        return False
    for w, sentences in enumerate(texts):
        for si, sentence in enumerate(sentences):
            own = [m.span for m in found if m.where == w and m.sentence == si]
            for b in blocked:
                for sp in _occurrences(b, sentence):
                    if not any(o0 <= sp[0] and sp[1] <= o1 for o0, o1 in own):
                        return True
    return any(_occurrences(b, s) for b in blocked for s in lesson)


def offers_for(scene: SceneText, candidates: Sequence[SavedObject], limit: int = 6) -> list[Offer]:
    """The drawings a scene may use, best first (named in the brief's objects, then most named)."""
    texts = [segments(t) for t in scene.objects] + [segments(" . ".join(scene.body))]
    lesson = [s for t in scene.lesson for s in segments(t)]
    mentions: list[Mention] = []
    for obj in candidates:
        # A drawing that is not built-in is used only with context words (approval asks for them).
        if obj.origin != "library" and not obj.context:
            continue
        found = _mentions(obj, texts)
        if not found or _meant_otherwise(obj, texts, found, lesson):
            continue
        # Context words count only outside the drawing's own names ("light" in "light bulb" is no
        # sign of a lesson about circuits).
        if obj.context and not any(_stem_in(c, _masked(texts, found) + lesson) for c in obj.context):
            continue
        mentions.extend(found)
    # The longer name wins where two drawings' names overlap ("plant cell" over "cell").
    kept: list[Mention] = []
    for m in mentions:
        longer = any(
            o.obj.id != m.obj.id
            and o.where == m.where
            and o.sentence == m.sentence
            and o.span[0] <= m.span[0]
            and m.span[1] <= o.span[1]
            and (o.span[1] - o.span[0]) > (m.span[1] - m.span[0])
            for o in mentions
        )
        if not longer:
            kept.append(m)
    by_id: dict[str, Offer] = {}
    n_objects = len(scene.objects)
    all_text = [s for t in texts for s in t] + lesson
    for m in kept:
        offer = by_id.setdefault(m.obj.id, Offer(m.obj))
        offer.mentions += 1
        if m.where < n_objects:
            oid = scene.objects[m.where].split(":", 1)[0].strip()
            if oid and oid not in offer.for_ids:
                offer.for_ids.append(oid)
    for offer in by_id.values():
        offer.preferred = sum(1 for p in offer.obj.prefer if _stem_in(p, all_text))
    ranked = sorted(
        by_id.values(),
        key=lambda o: (-(1 if o.for_ids else 0), -o.preferred, -o.mentions, o.obj.id),
    )
    return ranked[:limit]
