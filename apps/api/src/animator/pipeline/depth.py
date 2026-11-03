"""How much a lesson teaches in the time the learner chose (ADR-063).

- **Depth grows with length.** A 1-minute lesson is a Snapshot (one core idea, one picture that makes
  it click, one takeaway); 2 minutes are the Essentials (and why it works, a worked example, a
  prediction); 3 to 4 minutes a Deep dive; 5 to 7 minutes Mastery (chapters, each with its check);
  8 to 10 minutes a Masterclass (practice, common mistakes, a map of the ideas). A longer lesson goes
  deeper, not only wider: it is never a stretched short one.
- **Coverage first.** A small model call maps what the topic holds for this learner
  (`coverage.v1.md`): units with a kind, the time each needs to be taught well, and a priority that
  says which depth needs it (1: even a 1-minute lesson; 2: from 2 minutes; 3: from 3 to 4 minutes;
  4: from 5 to 7; 5: only a 10-minute masterclass). `fit` takes what this depth needs, most important
  first, while it fits the time, in teaching order (a unit after what it builds on), and turns it into
  the scenes the teacher writes, each with a word budget. What does not fit becomes "Go deeper".
- **A time budget in words.** Budgets use the pace the lesson's voice really speaks at (`pace.py`) and
  leave the quiet that ends every scene. The reviewer sees each scene's count against its budget, and
  a script still well over is trimmed (`trim.v1.md`) before any voice or drawing is paid for.

Without a coverage plan (switched off, or the call failed) the lesson still gets its depth and its
time budget: the scenes are counted from the time and the teacher chooses what they teach.

Lessons that flow (ADR-071, ANIMATOR_LESSON_FLOW): the map first reads what the learner asked for, the
lesson's shape (a process, a comparison, a problem, a procedure or a concept), the one example it follows
in every scene, and its spine: the complete path through the request (every stage of the journey, every
difference, every step of the solution). `fit` keeps the whole spine whatever the length, in its order
(a short lesson tells each step briefly; steps too short for a scene of their own share one), numbers
its steps, and adds the other units around it as the time allows. The lesson asks few questions (a
budget by depth) and stops for fewer quick checks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from animator.pipeline.models import (
    CoverageKind,
    CoveragePlan,
    CoverageUnit,
    LessonMap,
    ScriptScene,
    ScriptTrim,
    TeachingScript,
    TeachingStep,
)


@dataclass(frozen=True)
class Tier:
    id: str
    name: str
    upto_s: float  # lessons up to this long (seconds)
    summary: str  # for people (the Create page)
    brief: str  # for the teacher and the reviewer
    priority: int  # the coverage units it needs: priority up to this
    prefer: tuple[CoverageKind, ...]  # among units of equal priority, these first
    checkpoints: int  # quick questions in the lesson, at most
    least_s: float  # the least time one unit gets: fewer things, each explained properly
    # Lessons that flow (ADR-071): the brief for a lesson that walks one whole path, the questions its
    # narration may ask (the opening included), and the quick checks it stops for.
    flow_brief: str = ""
    questions: int = 1
    flow_checks: int = 0


TIERS: tuple[Tier, ...] = (
    Tier(
        "snapshot",
        "Snapshot",
        75.0,
        "One core idea, one picture that makes it click, one takeaway.",
        "an overview. Teach ONE core idea and make it click with one everyday picture or one concrete "
        "case, then land it in one memorable sentence. No derivations, no lists, no side topics, at most "
        "two new terms.",
        1,
        ("core", "intuition", "mechanism", "example"),
        1,
        12.0,
        "the whole path, briefly: each step in one or two sentences on the one concrete case, and a "
        "one-sentence takeaway. No derivations, no side topics, at most two new terms.",
        1,
        0,
    ),
    Tier(
        "essentials",
        "Essentials",
        150.0,
        "The core idea and why it works, a worked example, a quick prediction.",
        "the core idea and why it works (how it works in two or three clear steps), a worked example "
        "with simple numbers when the subject has numbers, and one question the learner predicts before "
        "you answer it. Name the common misconception only if the plan includes it.",
        2,
        ("core", "intuition", "example", "mechanism", "misconception", "application"),
        1,
        14.0,
        "the whole path with the why of each step, a worked example with simple numbers when the subject "
        "has numbers, and the takeaway. Name the common misconception only if the plan includes it.",
        1,
        1,
    ),
    Tier(
        "deep_dive",
        "Deep dive",
        270.0,
        "From what you know to the full idea: how it works step by step, a worked example, "
        "the common mistake, a real use.",
        "start from what the learner already knows, build the idea, explain how it works step by step, "
        "work an example with numbers, name and correct the common misconception, show a real-world "
        "use, check understanding, and recap.",
        3,
        ("core", "mechanism", "example", "intuition", "misconception", "prerequisite", "application"),
        2,
        16.0,
        "start from what the learner already knows, then walk the whole path with each step explained "
        "fully, work an example with numbers, name and correct the common misconception where it arises, "
        "show a real-world use, and recap the path.",
        2,
        1,
    ),
    Tier(
        "mastery",
        "Mastery",
        450.0,
        "Chapters, each with its example and check; limits and edge cases; how the ideas connect.",
        "two or three chapters, each a small lesson of its own (the idea, an example, a quick check), "
        "plus the misconception, a limitation or edge case, real uses, and a recap that connects the "
        "chapters.",
        4,
        (
            "core",
            "mechanism",
            "example",
            "misconception",
            "intuition",
            "prerequisite",
            "application",
            "extension",
        ),
        3,
        18.0,
        "two or three chapters along the same path, each a small lesson of its own (the idea, an "
        "example), plus the misconception, a limitation or edge case, real uses, and a recap that "
        "connects the chapters.",
        3,
        2,
    ),
    Tier(
        "masterclass",
        "Masterclass",
        float("inf"),
        "Everything in Mastery, plus a practice problem, common mistakes and a map of the ideas.",
        "everything a Mastery lesson has (chapters, each with an example and a quick check), plus a "
        "practice problem the learner tries before you solve it, the common mistakes, and a closing map "
        "of how the ideas connect.",
        5,
        (
            "core",
            "mechanism",
            "example",
            "misconception",
            "practice",
            "intuition",
            "prerequisite",
            "application",
            "extension",
        ),
        4,
        20.0,
        "everything a Mastery lesson has (chapters along the same path, each with an example), plus a "
        "practice problem the learner tries before you solve it, the common mistakes, and a closing map "
        "of how the ideas connect.",
        4,
        3,
    ),
)


def tier_for(duration_s: float) -> Tier:
    return next(t for t in TIERS if duration_s <= t.upto_s)


def tiers_json() -> list[dict[str, Any]]:
    """The tiers for the app (Create shows what a length gives)."""
    return [
        {
            "id": t.id,
            "name": t.name,
            "upto_s": None if t.upto_s == float("inf") else t.upto_s,
            "summary": t.summary,
        }
        for t in TIERS
    ]


# Canonical teaching order: what a learner needs before what builds on it.
KIND_RANK: dict[str, int] = {
    "prerequisite": 0,
    "intuition": 1,
    "core": 2,
    "mechanism": 3,
    "example": 4,
    "misconception": 5,
    "application": 6,
    "extension": 7,
    "practice": 8,
    "step": 3,
    "difference": 3,
}
STEP_OF: dict[str, TeachingStep] = {
    "prerequisite": "prior_knowledge",
    "intuition": "intuition",
    "core": "concept",
    "mechanism": "concept",
    "example": "example",
    "misconception": "misconception",
    "application": "apply",
    "extension": "concept",
    "practice": "apply",
    "step": "concept",
    "difference": "concept",
}

SCENE_SPEECH_S = 15.0  # a scene's narration, typically (12 to 20 seconds: one picture, 2 to 4 beats)
MERGE_BELOW_S = 9.0  # a hook or recap shorter than this opens or closes a scene instead of its own
STRETCH = 1.3  # what this depth needs is taken while it fits in this much of the time (then squeezed)
MOST_SCALE = 1.5  # ... and given at most this much more time than they need when they are few
LEAST_SCENE_WORDS = 12
# Models write about 5% over a word budget (measured: 1, 2 and 3-minute lessons +6%): the budgets aim
# that much under the time, so the usual overshoot lands on it.
AIM = 0.96
OVER = 1.05  # "at most": the budget plus 5%
TRIM_OVER = 1.10  # a script this far over its budget is trimmed
SCENE_TRIM_OVER = 1.12  # ... the scenes this far over their own budgets
LANDING = "land the lesson: the one idea in one memorable sentence, and where it shows up in real life"

# Lessons that flow (ADR-071): how each shape is taught, opened and landed, and what its steps are called.
STEP_LEAST_S = 7.0  # a step of the path told in one or two sentences: a short lesson still walks it all
MERGE_STEP_S = 9.0  # steps of the path shorter than this share a scene with the next one
MERGED_MOST_S = 17.0  # ... while the scene they share stays within a scene's usual length
STEP_EVERY_S = 20.0  # a lesson gives a scene of its own to one step of its path per this many seconds
SHAPES: dict[str, str] = {
    "process": "follow the through-line through every stage, in order, from start to finish",
    "comparison": "compare them side by side, one difference at a time, never one explained after the other",
    "problem": "solve it step by step with the reason for each step, then check the answer",
    "procedure": "do the steps in order on the one thing being made, each with what to do and why",
    "concept": "from the everyday case to the idea, how it works, and where it is used",
}
OPENINGS: dict[str, str] = {
    "process": "the opening: start the through-line and name the stages it will pass, in order",
    "comparison": "the opening: what both are for (and why they get confused), and the differences ahead",
    "problem": "the opening: read the problem (what it asks, what is given) and the plan to solve it",
    "procedure": "the opening: the goal, and the steps ahead in order",
    "concept": "the opening: start from the everyday case, and say what the learner will understand",
}
OPENING_ANY = "the opening: start the lesson's one example and name the path ahead, as a statement"
LANDINGS: dict[str, str] = {
    "process": "land the lesson: retrace the whole path in one breath, then the one idea and where it "
    "shows up in real life",
    "comparison": "land the lesson: the differences side by side in one breath, and when to choose which",
    "problem": "land the lesson: the answer and why it makes sense, the method in one sentence, and where "
    "this kind of problem shows up",
    "procedure": "land the lesson: the steps in order in one breath, the result, and where it is used",
    "concept": "land the lesson: retrace the path in one breath, the one idea in a memorable sentence, and "
    "where it shows up in real life",
}
LANDING_ANY = LANDINGS["concept"]
POINT_NAMES: dict[str, str] = {"comparison": "Difference", "concept": "Part"}  # otherwise "Step"
QUESTION = re.compile(r"[^.!?]*\?+")


@dataclass(frozen=True)
class PlannedScene:
    step: TeachingStep | None  # None: the teacher chooses
    teach: str
    words: int
    unit: str | None = None
    points: tuple[int, ...] = ()  # the steps of the lesson's path it teaches (ADR-071), 1-based


@dataclass(frozen=True)
class Blueprint:
    tier: Tier
    duration_s: float
    wps: float  # words per second of the lesson's voice
    scene_overhead_s: float  # the quiet that ends every scene
    scenes: tuple[PlannedScene, ...]
    hook: str | None = None
    big_idea: str | None = None
    chosen: tuple[str, ...] = ()
    deeper: tuple[CoverageUnit, ...] = ()
    units: tuple[CoverageUnit, ...] = ()
    # Lessons that flow (ADR-071): what the learner asked for, the lesson's shape, the example it
    # follows, and the units of its path in order (empty without a lesson map).
    flow: bool = False
    shape: str | None = None
    asked: str | None = None
    through_line: str | None = None
    spine: tuple[str, ...] = ()

    @property
    def words(self) -> int:
        return sum(s.words for s in self.scenes)

    @property
    def most_words(self) -> int:
        return round(self.words * OVER)

    @property
    def checkpoints(self) -> int:
        """Quick questions: never in the first scene, never in two scenes in a row; a flowing lesson
        (ADR-071) stops for fewer of them."""
        most = self.tier.flow_checks if self.flow else self.tier.checkpoints
        return max(0, min(most, len(self.scenes) // 2))

    @property
    def questions(self) -> int:
        """The most questions a flowing lesson's narration asks (ADR-071), the opening included."""
        return self.tier.questions

    @property
    def point_name(self) -> str:
        """What the steps of the path are called: Step, Difference or Part."""
        return POINT_NAMES.get(self.shape or "", "Step")

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "tier": self.tier.id,
            "name": self.tier.name,
            "summary": self.tier.summary,
            "duration_s": self.duration_s,
            "wps": self.wps,
            "scene_overhead_s": self.scene_overhead_s,
            "words": self.words,
            "checkpoints": self.checkpoints,
            "scenes": [
                {"step": s.step, "teach": s.teach, "words": s.words, "unit": s.unit}
                | ({"points": list(s.points)} if s.points else {})
                for s in self.scenes
            ],
            "hook": self.hook,
            "big_idea": self.big_idea,
            "chosen": list(self.chosen),
            "deeper": [{"id": u.id, "kind": u.kind, "title": u.title, "teach": u.teach} for u in self.deeper],
            "units": [u.model_dump() for u in self.units],
        }
        if self.flow:
            out |= {
                "flow": True,
                "shape": self.shape,
                "asked": self.asked,
                "through_line": self.through_line,
                "spine": list(self.spine),
                "questions": self.questions,
            }
        return out


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def scene_count(duration_s: float, overhead_s: float) -> int:
    return max(3, round(duration_s / (SCENE_SPEECH_S + overhead_s)))


def _usable(units: list[CoverageUnit]) -> list[CoverageUnit]:
    """Units with an id, a title and a plausible time (the model's numbers are clamped, not trusted)."""
    seen: set[str] = set()
    out: list[CoverageUnit] = []
    for u in units:
        uid = u.id.strip()
        if not uid or uid in seen or not u.title.strip():
            continue
        seen.add(uid)
        out.append(
            u.model_copy(
                update={
                    "id": uid,
                    "priority": int(_clamp(u.priority, 1, 5)),
                    "seconds": _clamp(float(u.seconds or 0.0), 8.0, 180.0),
                    "needs": [n.strip() for n in u.needs if n.strip() and n.strip() != uid],
                }
            )
        )
    return out


def _choose(units: list[CoverageUnit], tier: Tier, content_s: float) -> list[tuple[CoverageUnit, float]]:
    """The units this lesson teaches and the seconds each gets: what this depth needs, most important
    first, while it fits (each with the units it builds on); then, when time is left, what deeper
    lessons would add; squeezed or stretched (a little) to fill the time, in teaching order."""
    position = {u.id: i for i, u in enumerate(units)}
    by_id = {u.id: u for u in units}

    def rank(u: CoverageUnit) -> tuple[int, int, int]:
        prefer = tier.prefer
        return (u.priority, prefer.index(u.kind) if u.kind in prefer else len(prefer), position[u.id])

    def cost(u: CoverageUnit) -> float:
        return max(tier.least_s, u.seconds)

    picked: list[CoverageUnit] = []
    used = 0.0

    def group_of(u: CoverageUnit, most: int) -> list[CoverageUnit]:
        """`u` after the units it needs (those this lesson may teach and has not picked yet)."""
        out: list[CoverageUnit] = []
        seen = {p.id for p in picked}

        def visit(x: CoverageUnit, depth: int) -> None:
            if x.id in seen or depth > 6:
                return
            seen.add(x.id)
            for need in x.needs:
                n = by_id.get(need)
                if n is not None and n.priority <= most:
                    visit(n, depth + 1)
            out.append(x)

        visit(u, 0)
        return out

    def take(most: int, limit: float) -> None:
        nonlocal used
        for u in sorted((u for u in units if u.priority <= most), key=rank):
            if any(p.id == u.id for p in picked):
                continue
            group = group_of(u, most)
            extra = sum(cost(g) for g in group)
            if not picked or used + extra <= limit:
                picked.extend(group)
                used += extra

    top = min(units, key=rank)
    take(max(tier.priority, top.priority), content_s * STRETCH)
    if used < content_s * 0.8:
        take(5, content_s)  # time left: what a deeper lesson would add, most important first
    # Too many for the time (each unit keeps its least time): drop the least important, sparing the
    # most important unit and what the others build on.
    while len(picked) > 1 and len(picked) * tier.least_s > content_s:
        others = [p for p in picked if p.id != top.id]
        free = [p for p in others if not any(p.id in q.needs for q in picked if q is not p)]
        picked.remove(max(free or others, key=rank))
    total = sum(cost(u) for u in picked) or 1.0
    scale = min(1.0, content_s / total)  # squeezed to fit; stretching (a little) is left to fit()
    order = sorted(picked, key=lambda u: (KIND_RANK.get(u.kind, 9), position[u.id]))
    return [(u, cost(u) * scale) for u in _after_needs(order)]


def _after_needs(order: list[CoverageUnit]) -> list[CoverageUnit]:
    """`order`, with every unit moved after the units it needs (bounded: needs may loop)."""
    order = list(order)
    for _ in range(len(order) * len(order)):
        place = {u.id: i for i, u in enumerate(order)}
        move = next(
            (
                (i, max(place[n] for n in u.needs if n in place and place[n] > i))
                for i, u in enumerate(order)
                if any(n in place and place[n] > i for n in u.needs)
            ),
            None,
        )
        if move is None:
            break
        i, j = move
        order.insert(i, order.pop(j))
    return order


EARLY = ("prerequisite", "intuition")  # taught before the step that needs them
LATE = ("application", "extension", "practice")  # taught after the path


def _path_of(
    lesson: LessonMap, units: list[CoverageUnit]
) -> tuple[list[CoverageUnit], list[CoverageUnit], list[CoverageUnit]]:
    """The lesson's spine (unknown and repeated ids left out) as its numbered path and the spine units
    taught before and after it unnumbered: a comparison numbers only its differences (what both share
    is not one of them); every other lesson numbers its whole spine."""
    by_id = {u.id: u for u in units}
    ids = dict.fromkeys(i.strip() for i in lesson.spine)
    spine = [by_id[i] for i in ids if i in by_id]
    if lesson.shape != "comparison" or not any(u.kind == "difference" for u in spine):
        return spine, [], []
    first = next(i for i, u in enumerate(spine) if u.kind == "difference")
    path = [u for u in spine if u.kind == "difference"]
    others = [u for u in spine if u.kind != "difference"]
    return path, [u for u in others if spine.index(u) < first], [u for u in others if spine.index(u) > first]


def _choose_path(
    units: list[CoverageUnit],
    path: list[CoverageUnit],
    tier: Tier,
    content_s: float,
    before: list[CoverageUnit] | None = None,
    after: list[CoverageUnit] | None = None,
) -> list[tuple[CoverageUnit, float]]:
    """A flowing lesson's units and the seconds each gets (ADR-071): the whole path, whatever the length
    (squeezed when the time is short, each step keeping a sentence or two), with the spine's unnumbered
    units `before` and `after` it, and around it what this depth needs while it fits, then (time left)
    what a deeper lesson would add."""
    before, after = before or [], after or []
    position = {u.id: i for i, u in enumerate(units)}
    by_id = {u.id: u for u in units}
    spine = [*before, *path, *after]
    on_spine = {u.id for u in spine}

    def rank(u: CoverageUnit) -> tuple[int, int, int]:
        prefer = tier.prefer
        return (u.priority, prefer.index(u.kind) if u.kind in prefer else len(prefer), position[u.id])

    def cost(u: CoverageUnit) -> float:
        return max(STEP_LEAST_S, u.seconds) if u.id in on_spine else max(tier.least_s, u.seconds)

    extras: list[CoverageUnit] = []
    used = sum(cost(u) for u in spine)

    def group_of(u: CoverageUnit, most: int) -> list[CoverageUnit]:
        """`u` after the units it needs that are not taught yet (bounded: needs may loop)."""
        out: list[CoverageUnit] = []
        seen = on_spine | {e.id for e in extras}

        def visit(x: CoverageUnit, depth: int) -> None:
            if x.id in seen or depth > 6:
                return
            seen.add(x.id)
            for need in x.needs:
                n = by_id.get(need)
                if n is not None and n.priority <= most:
                    visit(n, depth + 1)
            out.append(x)

        visit(u, 0)
        return out

    def take(most: int) -> None:
        nonlocal used
        for u in sorted((u for u in units if u.id not in on_spine and u.priority <= most), key=rank):
            if any(e.id == u.id for e in extras):
                continue
            group = group_of(u, most)
            extra = sum(cost(g) for g in group)
            if used + extra <= content_s:
                extras.extend(group)
                used += extra

    take(tier.priority)
    if used < content_s * 0.8:
        take(5)  # time left: what a deeper lesson would add, most important first
    scale = min(1.0, content_s / (used or 1.0))  # a long path squeezed to fit
    return [(u, cost(u) * scale) for u in _place(spine, extras, path[0].id if path else None)]


def _place(
    spine: list[CoverageUnit], extras: list[CoverageUnit], first: str | None = None
) -> list[CoverageUnit]:
    """The units in teaching order: the spine in its own order; what the learner needs first before the
    step that needs it (or before the path, which starts at `first`); an example, a misconception or a
    closer look right after the step it builds on; uses and extensions after the spine."""
    order = list(spine)
    placed: set[str] = set()
    for e in sorted(extras, key=lambda u: KIND_RANK.get(u.kind, 9)):
        here = {o.id: i for i, o in enumerate(order)}
        anchors = [here[n] for n in e.needs if n in here]
        if e.kind in EARLY:
            users = [i for i, o in enumerate(order) if e.id in o.needs]
            at = min(users) if users else here.get(first or "", 0)
        elif e.kind in LATE or not anchors:
            at = len(order)
        else:
            at = max(anchors) + 1
            while at < len(order) and order[at].id in placed:  # after what is already placed there
                at += 1
        order.insert(at, e)
        placed.add(e.id)
    return order


# step, what it teaches, seconds, unit, the steps of the path it teaches (ADR-071)
Section = tuple[TeachingStep | None, str, float, str | None, tuple[int, ...]]


THEN = "; then "  # joins the steps a section of a flowing lesson holds


def _scenes_of(sections: list[Section]) -> list[Section]:
    """Sections cut into scenes of about SCENE_SPEECH_S (a long section becomes several scenes). A
    section holding several steps of a lesson's path is cut between its steps, never inside one."""
    out: list[Section] = []
    for step, teach, seconds, unit, points in sections:
        k = max(1, round(seconds / SCENE_SPEECH_S))
        pieces = teach.split(THEN)
        if k > 1 and len(points) > 1 and len(pieces) == len(points):
            k = min(k, len(pieces))
            for j in range(k):
                lo, hi = j * len(pieces) // k, (j + 1) * len(pieces) // k
                share = seconds * (hi - lo) / len(pieces)
                out.append((step, THEN.join(pieces[lo:hi]), share, unit, points[lo:hi]))
            continue
        for j in range(k):
            out.append(
                (step, f"{teach} (part {j + 1} of {k})" if k > 1 else teach, seconds / k, unit, points)
            )
    return out


def steps_most(duration_s: float) -> int:
    """How many steps of its path a lesson this long gives a scene each (ADR-071): one per
    STEP_EVERY_S, at least 2, at most 10. Further steps share scenes."""
    return max(2, min(10, round(duration_s / STEP_EVERY_S)))


def _group_steps(sections: list[Section], most: int) -> list[Section]:
    """A path with more steps than `most` keeps every step: neighbouring steps share a scene, the
    shortest pair first, until no more than `most` sections hold steps."""
    out = list(sections)
    while sum(1 for s in out if s[4]) > most:
        pairs = [i for i in range(len(out) - 1) if out[i][4] and out[i + 1][4]]
        if not pairs:
            break
        i = min(pairs, key=lambda k: out[k][2] + out[k + 1][2])
        a, b = out[i], out[i + 1]
        out[i : i + 2] = [(a[0], a[1] + THEN + b[1], a[2] + b[2], a[3], a[4] + b[4])]
    return out


def _merge_steps(sections: list[Section]) -> list[Section]:
    """Steps of a lesson's path too short for a scene of their own share one with the next step: a short
    lesson walks the whole path, a few steps a scene (ADR-071). Other sections are never merged."""
    out: list[Section] = []
    for sec in sections:
        if out:
            step, teach, s, unit, points = out[-1]
            if (
                points
                and sec[4]
                and s < MERGE_STEP_S
                and sec[2] < MERGE_STEP_S
                and s + sec[2] <= MERGED_MOST_S
            ):
                out[-1] = (step, teach + THEN + sec[1], s + sec[2], unit, points + sec[4])
                continue
        out.append(sec)
    return out


def _layout(sections: list[Section], factor: float) -> list[Section]:
    """The scenes the sections make at this stretch."""
    return _scenes_of(_merge_steps([(st, t, s * factor, u, p) for st, t, s, u, p in sections]))


def fit(
    coverage: CoveragePlan | LessonMap | None,
    duration_s: float,
    wps: float,
    overhead_s: float,
    flow: bool = False,
) -> Blueprint:
    """The plan for a lesson of `duration_s` spoken at `wps` words per second, with `overhead_s` of
    quiet after every scene: its depth, its scenes (what each teaches, its word budget) and what is
    left for a longer lesson. A flowing lesson (`flow`, ADR-071) planned from a lesson map walks the
    map's whole path, its steps numbered."""
    tier = tier_for(duration_s)
    units = _usable(coverage.units) if coverage is not None else []
    n = scene_count(duration_s, overhead_s)
    if coverage is None or not units:
        return _plain(tier, duration_s, wps, overhead_s, n, flow)
    lesson = coverage if flow and isinstance(coverage, LessonMap) else None
    shape = lesson.shape if lesson is not None else None
    hook_s = _clamp(0.08 * duration_s, 4.0, 15.0)
    recap_s = _clamp(0.10 * duration_s, 5.0, 30.0)
    speech = max(10.0, duration_s - n * overhead_s)
    content = max(tier.least_s, speech - hook_s - recap_s)
    path, before, after = _path_of(lesson, units) if lesson is not None else ([], [], [])
    chosen = (
        _choose_path(units, path, tier, content, before, after) if path else _choose(units, tier, content)
    )
    step_no = {u.id: k for k, u in enumerate(path, 1)}
    name = POINT_NAMES.get(shape or "", "Step")
    sections: list[Section] = []
    for u, s in chosen:
        k = step_no.get(u.id)
        label = f"{name} {k} of {len(path)}, " if k else ""
        sections.append(
            (STEP_OF.get(u.kind, "concept"), f"{label}{u.title}: {u.teach}", s, u.id, (k,) if k else ())
        )
    sections = _group_steps(sections, steps_most(duration_s))
    opening = OPENINGS.get(shape or "", OPENING_ANY)
    landing = LANDINGS.get(shape or "", LANDING_ANY) if flow else LANDING
    # A short hook opens the first scene and a short recap closes the last; longer ones are scenes.
    lead, tail = hook_s < MERGE_BELOW_S, recap_s < MERGE_BELOW_S
    if lead:
        step, teach, s, unit, points = sections[0]
        sections[0] = (step, teach, s + hook_s, unit, points)
    else:
        hook = opening if flow else "the hook: a question or surprise that makes the learner want the answer"
        sections.insert(0, ("hook", hook, hook_s, None, ()))
    if tail:
        step, teach, s, unit, points = sections[-1]
        sections[-1] = (step, teach, s + recap_s, unit, points)
    else:
        sections.append(("recap", landing, recap_s, None, ()))
    # The sections fill the speech time: the time asked less the quiet after each scene, which depends
    # on how many scenes they make. A thin plan is stretched by at most MOST_SCALE (a shorter lesson
    # rather than a padded one); scenes are cut after stretching, so they keep their usual length.
    total = sum(s for _, _, s, _, _ in sections) or 1.0
    factor = 1.0
    for _ in range(4):
        n = len(_layout(sections, factor))
        better = min(max(10.0, duration_s - n * overhead_s) / total, MOST_SCALE)
        if abs(better - factor) < 0.005:
            break
        factor = better
    scenes = _layout(sections, factor)
    if lead:
        _, teach, s, unit, points = scenes[0]
        first = f"first {opening}, then {teach}" if flow else f"open with the hook, then {teach}"
        scenes[0] = ("hook", first, s, unit, points)
    if tail:
        step, teach, s, unit, points = scenes[-1]
        scenes[-1] = (step, f"{teach}; then {landing}", s, unit, points)
    speech = max(10.0, duration_s - len(scenes) * overhead_s)
    fill = min(1.0, speech / (sum(s for _, _, s, _, _ in scenes) or 1.0))  # never longer than the time
    planned = tuple(
        PlannedScene(step, teach, max(LEAST_SCENE_WORDS, round(s * fill * wps * AIM)), unit, points)
        for step, teach, s, unit, points in scenes
    )
    picked = {u.id for u, _ in chosen}
    deeper = sorted(
        (u for u in units if u.id not in picked),
        key=lambda u: (u.priority, KIND_RANK.get(u.kind, 9)),
    )
    return Blueprint(
        tier,
        duration_s,
        wps,
        overhead_s,
        planned,
        coverage.hook.strip() or None,
        coverage.big_idea.strip() or None,
        tuple(u.id for u, _ in chosen),
        tuple(deeper),
        tuple(units),
        flow=flow,
        shape=shape,
        asked=(lesson.asked.strip() or None) if lesson is not None else None,
        through_line=(lesson.through_line.strip() or None) if lesson is not None else None,
        spine=tuple(u.id for u in path),
    )


def _plain(
    tier: Tier, duration_s: float, wps: float, overhead_s: float, n: int, flow: bool = False
) -> Blueprint:
    """Without a coverage plan: the depth, the scene count and each scene's budget."""
    speech = max(10.0, duration_s - n * overhead_s)
    each = max(LEAST_SCENE_WORDS, round(speech * wps * AIM / n))
    scenes = [PlannedScene(None, "", each) for _ in range(n)]
    scenes[0] = PlannedScene("hook", OPENING_ANY if flow else "open with the hook", each)
    scenes[-1] = PlannedScene("recap", LANDING_ANY if flow else LANDING, each)
    return Blueprint(tier, duration_s, wps, overhead_s, tuple(scenes), flow=flow)


# ---------------------------------------------------------------------- what the models are told


def length_name(duration_s: float) -> str:
    """'a 30-second', 'a 1-minute', 'a 2.5-minute' (lesson)."""
    if duration_s < 60:
        return f"a {round(duration_s)}-second"
    return f"a {round(duration_s / 60, 1):g}-minute"


def teacher_lines(bp: Blueprint) -> list[str]:
    """The plan for this length, for the teacher's (and the reviewer's) message. A flowing lesson
    (ADR-071) also says what the learner asked for, the lesson's shape, the example it follows, its path
    and how many questions it may ask."""
    brief = bp.tier.flow_brief if bp.flow else bp.tier.brief
    lines = [f"Depth: {bp.tier.name}, {length_name(bp.duration_s)} lesson: {brief}"]
    if bp.flow:
        lines += flow_lines(bp)
    elif bp.hook:
        lines.append(f"Hook: {bp.hook}")
    if bp.big_idea:
        lines.append(f"The one idea to land: {bp.big_idea}")
    if bp.flow and bp.spine:
        titles = {u.id: u.title for u in bp.units}
        steps = "; ".join(f"{k}. {titles.get(i, i)}" for k, i in enumerate(bp.spine, 1))
        lines.append(f"The path, in order ({len(bp.spine)} {bp.point_name.lower()}s): {steps}.")
    lines.append(
        f"Plan for this length: exactly {len(bp.scenes)} scenes, in this order, "
        "each close to its word budget:"
    )
    for i, s in enumerate(bp.scenes):
        what = f"{s.step}: {s.teach}" if s.step and s.teach else (s.teach or "your choice")
        lines.append(f"{i + 1}. {what} (about {s.words} words)")
    lines.append(
        f"Narration length: about {bp.words} words in total (at most {bp.most_words}). The voice speaks "
        f"about {bp.wps:.1f} words per second, so this fills the time the learner chose."
    )
    if bp.flow:
        lines.append(
            f"Questions: at most {bp.questions} in the whole narration, the opening included, and fewer is "
            "better. Explain with statements."
        )
    if bp.deeper:
        lines.append(
            "Left for a longer lesson (do not teach these): "
            + "; ".join(u.title for u in bp.deeper[:8])
            + "."
        )
    return lines


def flow_lines(bp: Blueprint) -> list[str]:
    """What a flowing lesson (ADR-071) answers and how: the request, its shape, the example every scene
    follows and the opening. Without a lesson map, the teacher reads them from the request."""
    lines = [
        f"What the learner asked for: {bp.asked}"
        if bp.asked
        else "What the learner asked for: read it from the request above, and teach exactly that."
    ]
    if bp.shape:
        lines.append(f"Lesson shape: {bp.shape}: {SHAPES.get(bp.shape, '')}.")
    else:
        lines.append(
            "Lesson shape: choose it from the request (process, comparison, problem, procedure or concept) "
            "and teach in that shape."
        )
    if bp.through_line:
        lines.append(f"Through-line (the one example every scene follows): {bp.through_line}")
    if bp.hook:
        lines.append(f"Opening: {bp.hook}")
    return lines


def questions_in(script: TeachingScript) -> list[tuple[int, str]]:
    """The questions the narration asks (ADR-071), in order: (scene index, the question)."""
    out: list[tuple[int, str]] = []
    for i, sc in enumerate(script.scenes):
        for b in sc.beats:
            for m in QUESTION.finditer(b.say):
                q = m.group(0).strip()
                if q.strip("?"):
                    out.append((i, q))
    return out


def asks(scene: ScriptScene) -> int:
    """How many questions a scene's narration asks."""
    return sum(1 for b in scene.beats for m in QUESTION.finditer(b.say) if m.group(0).strip().strip("?"))


def question_note(script: TeachingScript, bp: Blueprint) -> str:
    """The questions the script asks against the most a flowing lesson may (models cannot count)."""
    asked = questions_in(script)
    if len(asked) <= bp.questions:
        return f"Questions: the narration asks {len(asked)} (at most {bp.questions}). Keep it so."
    where = ", ".join(str(i + 1) for i in sorted({i for i, _ in asked}))
    return (
        f"Questions: the narration asks {len(asked)} questions (in scenes {where}); at most {bp.questions} "
        "may stay. Keep only real predictions answered at once, and turn the others into statements."
    )


def extra_questions(script: TeachingScript, bp: Blueprint) -> dict[int, list[str]]:
    """The questions over a flowing lesson's budget (the later ones), by scene index: to be turned into
    statements before anything is voiced or drawn. None for other lessons."""
    if not bp.flow:
        return {}
    out: dict[int, list[str]] = {}
    for i, q in questions_in(script)[bp.questions :]:
        out.setdefault(i, []).append(q)
    return out


def scene_words(scene: ScriptScene) -> int:
    return sum(len(b.say.split()) for b in scene.beats)


def script_words(script: TeachingScript) -> int:
    return sum(scene_words(sc) for sc in script.scenes)


def budgets(script: TeachingScript, bp: Blueprint) -> list[int]:
    """Each scene's word budget: the plan's when the script has the planned scenes, otherwise the
    total shared in proportion to the scenes' lengths."""
    if len(script.scenes) == len(bp.scenes):
        return [s.words for s in bp.scenes]
    words = [max(1, scene_words(sc)) for sc in script.scenes]
    total = sum(words)
    return [max(LEAST_SCENE_WORDS, round(bp.words * w / total)) for w in words]


def budget_note(script: TeachingScript, bp: Blueprint) -> str:
    """The script's measured length against its budgets, for the reviewer (models cannot count)."""
    words = script_words(script)
    counts = []
    for i, (sc, b) in enumerate(zip(script.scenes, budgets(script, bp), strict=True)):
        w = scene_words(sc)
        tip = (
            f" (cut {w - b})"
            if w > b * SCENE_TRIM_OVER
            else f" (room for {b - w} more)"
            if w < b * 0.75
            else ""
        )
        counts.append(f"{i + 1}: {w} of {b}{tip}")
    planned = (
        ""
        if len(script.scenes) == len(bp.scenes)
        else f" The plan has {len(bp.scenes)} scenes; this script has {len(script.scenes)}."
    )
    if words > bp.most_words:
        head = (
            f"Length: the script has {words} words of narration; the budget is {bp.words} (at most "
            f"{bp.most_words}): longer, and the lesson runs past the time the learner chose. Shorten the "
            "scenes marked 'cut': keep every step of the explanation, the example and the misconception; "
            "cut repetition, filler and side remarks."
        )
    elif words < bp.words * 0.85:
        more = (
            "the why of each step, a clearer example" if bp.flow else "why, a prediction, a clearer example"
        )
        head = (
            f"Length: the script has {words} words of narration; the budget is {bp.words}. Use the room "
            f"to explain more fully ({more}), up to about that length."
        )
    else:
        head = (
            f"Length: the script has {words} words of narration, close to the budget of {bp.words}. "
            "Keep it so."
        )
    note = head + planned + "\nScene words against their budgets: " + "; ".join(counts) + "."
    return note + "\n" + question_note(script, bp) if bp.flow else note


def over_budget(script: TeachingScript, bp: Blueprint) -> list[int]:
    """Scenes to trim (0-based) when the script runs well over its budget: those well over their own."""
    if script_words(script) <= bp.words * TRIM_OVER:
        return []
    return [
        i
        for i, (sc, b) in enumerate(zip(script.scenes, budgets(script, bp), strict=True))
        if scene_words(sc) > b * SCENE_TRIM_OVER
    ]


def trim_lines(script: TeachingScript, bp: Blueprint, scenes: list[int]) -> list[str]:
    per = budgets(script, bp)
    lines = [
        f"The lesson's narration has {script_words(script)} words; it must fit about {bp.words} "
        f"(at most {bp.most_words}). Shorten these scenes to their budgets:"
    ]
    for i in scenes:
        sc = script.scenes[i]
        beats = [{"say": b.say, "show": b.show} for b in sc.beats]
        lines.append(
            f"Scene {i + 1} ({sc.step}, '{sc.title}'): {scene_words(sc)} words, budget {per[i]}. "
            f"Beats: {beats}"
        )
    return lines


def edit_lines(
    script: TeachingScript, bp: Blueprint, over: list[int], restate: dict[int, list[str]]
) -> list[str]:
    """The edit a flowing lesson's script needs (ADR-071): the scenes to shorten to their budgets and the
    questions to turn into statements, with each scene's beats."""
    per = budgets(script, bp)
    lines = []
    if over:
        lines.append(
            f"The lesson's narration has {script_words(script)} words; it must fit about {bp.words} "
            f"(at most {bp.most_words}). Shorten the scenes marked 'shorten' to their budgets."
        )
    if restate:
        lines.append(
            f"The narration may ask at most {bp.questions} questions: turn the questions listed into "
            "statements."
        )
    for i in sorted(set(over) | set(restate)):
        sc = script.scenes[i]
        todo = "shorten it to its budget" if i in over else "keep about its length"
        if i in restate:
            todo += "; turn into statements: " + " | ".join(f'"{q}"' for q in restate[i])
        beats = [{"say": b.say, "show": b.show} for b in sc.beats]
        lines.append(
            f"Scene {i + 1} ({sc.step}, '{sc.title}'): {scene_words(sc)} words, budget {per[i]} ({todo}). "
            f"Beats: {beats}"
        )
    return lines


def apply_trim(
    script: TeachingScript, trim: ScriptTrim, allowed: list[int], restate: dict[int, list[str]] | None = None
) -> tuple[TeachingScript, int]:
    """The script with the edited scenes' beats in place (only scenes asked for, still spoken): a scene
    to shorten only when shorter; a scene whose questions were to become statements (ADR-071) when it
    asks fewer and is not much longer. The script and how many scenes changed."""
    restate = restate or {}
    scenes = list(script.scenes)
    changed = 0
    for t in trim.scenes:
        i = t.scene - 1
        if i not in allowed or not 0 <= i < len(scenes):
            continue
        beats = [b for b in t.beats if b.say.strip()]
        if not beats or len(beats) > 5:
            continue
        new = scenes[i].model_copy(update={"beats": beats})
        if i in restate:
            if asks(new) >= asks(scenes[i]) or scene_words(new) > scene_words(scenes[i]) * 1.15:
                continue
        elif scene_words(new) >= scene_words(scenes[i]):
            continue
        scenes[i] = new
        changed += 1
    return script.model_copy(update={"scenes": scenes}), changed
