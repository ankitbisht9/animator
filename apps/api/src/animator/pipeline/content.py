"""Content first (ADR-072): what a lesson teaches is prepared before it is written for the narrator.

A subject expert (`content.v1.md`) writes the content brief for exactly the scenes the plan for the
lesson's length lists: for each scene its point in plain words, the reason behind it, a concrete
example, the new term with an everyday meaning, an analogy (what maps to what, where it stops) and the
likely misconception where they help, and the picture that makes it visible; for the lesson, its subject
family, what the learner starts from, the takeaway, the worked solution of a problem or the features of
a comparison. The teacher then decides only how to say it, the reviewer checks the words against what
code measures here (sentences too long to follow by ear, terms used before they are explained), and the
visual director draws the brief's pictures. Subject families get their own guidance
(`playbooks.v1.md`).
"""

from __future__ import annotations

import re

from animator.pipeline.models import ContentBrief, ScriptScene, TeachingScript

LONG_WORDS = 22  # a sentence longer than this is hard to follow by ear (one hearing, no rereading)
SENTENCE = re.compile(r"(?<=[.!?])\s+")


def playbook_sections(text: str) -> dict[str, str]:
    """The subject playbooks by family (`## family` headings), each with its content and pictures."""
    out: dict[str, str] = {}
    for part in re.split(r"^## ", text, flags=re.M)[1:]:
        name, _, body = part.partition("\n")
        out[name.strip()] = body.strip()
    return out


def pictures_of(section: str) -> str:
    """The pictures part of a playbook section ("Pictures:" and what follows)."""
    _, sep, rest = section.partition("Pictures:")
    return rest.strip() if sep else ""


def brief_lines(brief: ContentBrief, playbook: str | None) -> list[str]:
    """The content brief as the teacher's (and the reviewer's) message gives it: the lesson's content,
    scene by scene, then the subject's guidance."""
    lines = [
        "Content brief (the lesson's content, prepared by a subject expert: teach exactly this, scene by "
        "scene):",
        f"Subject family: {brief.field}. The learner starts from: {brief.learner_start}",
        f"Takeaway: {brief.takeaway}",
    ]
    if brief.terms:
        lines.append(
            "Terms (one name each, used every time; introduce each in its scene with its everyday meaning): "
            + "; ".join(f"{t.term}: {t.meaning} (scene {t.scene})" for t in brief.terms)
        )
    for sc in sorted(brief.scenes, key=lambda s: s.scene):
        parts = [f"Point: {sc.point}", f"Because: {sc.because}", f"Example: {sc.example}"]
        if sc.analogy.strip():
            parts.append(f"Analogy: {sc.analogy}")
        if sc.misconception.strip():
            parts.append(f"Misconception: {sc.misconception}")
        parts.append(f"Picture: {sc.picture}")
        lines.append(f"Scene {sc.scene}. " + " | ".join(parts))
    if brief.worked:
        lines.append(
            "Worked solution (say each step's goal, do its work, give its reason, in this order): "
            + " ".join(
                f"{k}. {w.label}: {w.work} (because {w.reason.rstrip('.')})."
                for k, w in enumerate(brief.worked, 1)
            )
        )
    if brief.contrast:
        lines.append(
            "Features compared (both sides together, one feature at a time): "
            + "; ".join(f"{c.feature}: {c.first} | {c.second}" for c in brief.contrast)
        )
    if brief.choose_when.strip():
        lines.append(f"When to choose which: {brief.choose_when}")
    if playbook:
        lines.append(f"How {brief.field} is taught best:\n{playbook}")
    return lines


def picture_lines(brief: ContentBrief, pictures: str | None) -> list[str]:
    """What the visual director is told of the content brief: each scene's picture, the terms to label
    as they are said, and the subject's guidance for pictures."""
    lines = [
        "Pictures from the content brief (each scene's central picture): "
        + " ".join(f"Scene {sc.scene}: {sc.picture}" for sc in sorted(brief.scenes, key=lambda s: s.scene))
    ]
    if brief.terms:
        lines.append(
            "Terms to label on the picture as they are first said: "
            + "; ".join(f"{t.term} (scene {t.scene})" for t in brief.terms)
        )
    if pictures:
        lines.append(f"Pictures that teach {brief.field} best:\n{pictures}")
    return lines


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE.split(text.replace("**", "").strip()) if s.strip()]


def long_sentences(script: TeachingScript) -> list[tuple[int, str, int]]:
    """Sentences too long to follow by ear: (scene index, the sentence, its words)."""
    out: list[tuple[int, str, int]] = []
    for i, sc in enumerate(script.scenes):
        for b in sc.beats:
            for s in _sentences(b.say):
                n = len(s.split())
                if n > LONG_WORDS:
                    out.append((i, s, n))
    return out


def long_by_scene(script: TeachingScript) -> dict[int, list[str]]:
    """The sentences too long to follow by ear, by scene index: to be split before anything is voiced."""
    out: dict[int, list[str]] = {}
    for i, s, _ in long_sentences(script):
        out.setdefault(i, []).append(s)
    return out


def longs_in(scene: ScriptScene) -> int:
    """How many sentences of a scene are too long to follow by ear."""
    return sum(1 for b in scene.beats for s in _sentences(b.say) if len(s.split()) > LONG_WORDS)


def early_terms(script: TeachingScript, brief: ContentBrief) -> list[tuple[str, int, int]]:
    """Terms the narration uses in a scene before the one the brief explains them in: (term, the scene
    it is first used in, the scene that explains it), both 1-based."""
    out: list[tuple[str, int, int]] = []
    for t in brief.terms:
        word = t.term.strip()
        if not word:
            continue
        pattern = re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)", re.I)
        first = next(
            (
                i
                for i, sc in enumerate(script.scenes)
                if any(pattern.search(b.say.replace("**", "")) for b in sc.beats)
            ),
            None,
        )
        if first is not None and first + 1 < t.scene:
            out.append((word, first + 1, t.scene))
    return out


def word_note(script: TeachingScript, brief: ContentBrief | None, most_terms: int | None = None) -> str:
    """What code measures in the narration's words, for the reviewer (models cannot count): sentences too
    long to follow by ear, terms used before the scene that explains them, and more new terms than a
    lesson of this depth holds (`most_terms`)."""
    long = long_sentences(script)
    if long:
        shown = "; ".join(
            f'scene {i + 1}: "{s if len(s) <= 90 else s[:87] + "..."}" ({n} words)' for i, s, n in long[:6]
        )
        more = f" (and {len(long) - 6} more)" if len(long) > 6 else ""
        lines = [
            f"Words: {len(long)} sentences are longer than {LONG_WORDS} words, too long to follow by ear; "
            f"split each into two: {shown}{more}."
        ]
    else:
        lines = [f"Words: no sentence is longer than {LONG_WORDS} words. Keep it so."]
    terms = [k.term for k in script.key_terms]
    if most_terms is not None and len(terms) > most_terms:
        lines.append(
            f"New terms: the script introduces {len(terms)} ({', '.join(terms)}); a lesson of this depth "
            f"holds at most {most_terms}. Keep the ones the learner truly needs and say the others in plain "
            "words."
        )
    if brief is not None:
        early = early_terms(script, brief)
        if early:
            lines.append(
                "Terms used before the scene that explains them (give the everyday meaning where a term is "
                "first said, or keep it for its scene): "
                + "; ".join(f"'{t}' first said in scene {a}, explained in scene {b}" for t, a, b in early)
                + "."
            )
    return "\n".join(lines)
