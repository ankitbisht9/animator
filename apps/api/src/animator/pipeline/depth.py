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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from animator.pipeline.models import (
    CoverageKind,
    CoveragePlan,
    CoverageUnit,
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


@dataclass(frozen=True)
class PlannedScene:
    step: TeachingStep | None  # None: the teacher chooses
    teach: str
    words: int
    unit: str | None = None


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

    @property
    def words(self) -> int:
        return sum(s.words for s in self.scenes)

    @property
    def most_words(self) -> int:
        return round(self.words * OVER)

    @property
    def checkpoints(self) -> int:
        """Quick questions: never in the first scene, never in two scenes in a row."""
        return max(0, min(self.tier.checkpoints, len(self.scenes) // 2))

    def to_json(self) -> dict[str, Any]:
        return {
            "tier": self.tier.id,
            "name": self.tier.name,
            "summary": self.tier.summary,
            "duration_s": self.duration_s,
            "wps": self.wps,
            "scene_overhead_s": self.scene_overhead_s,
            "words": self.words,
            "checkpoints": self.checkpoints,
            "scenes": [
                {"step": s.step, "teach": s.teach, "words": s.words, "unit": s.unit} for s in self.scenes
            ],
            "hook": self.hook,
            "big_idea": self.big_idea,
            "chosen": list(self.chosen),
            "deeper": [{"id": u.id, "kind": u.kind, "title": u.title, "teach": u.teach} for u in self.deeper],
            "units": [u.model_dump() for u in self.units],
        }


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


Section = tuple[TeachingStep | None, str, float, str | None]  # step, what it teaches, seconds, unit


def _scenes_of(sections: list[Section]) -> list[Section]:
    """Sections cut into scenes of about SCENE_SPEECH_S (a long section becomes several scenes)."""
    out: list[Section] = []
    for step, teach, seconds, unit in sections:
        k = max(1, round(seconds / SCENE_SPEECH_S))
        for j in range(k):
            out.append((step, f"{teach} (part {j + 1} of {k})" if k > 1 else teach, seconds / k, unit))
    return out


def fit(coverage: CoveragePlan | None, duration_s: float, wps: float, overhead_s: float) -> Blueprint:
    """The plan for a lesson of `duration_s` spoken at `wps` words per second, with `overhead_s` of
    quiet after every scene: its depth, its scenes (what each teaches, its word budget) and what is
    left for a longer lesson."""
    tier = tier_for(duration_s)
    units = _usable(coverage.units) if coverage is not None else []
    n = scene_count(duration_s, overhead_s)
    if coverage is None or not units:
        return _plain(tier, duration_s, wps, overhead_s, n)
    hook_s = _clamp(0.08 * duration_s, 4.0, 15.0)
    recap_s = _clamp(0.10 * duration_s, 5.0, 30.0)
    speech = max(10.0, duration_s - n * overhead_s)
    content = max(tier.least_s, speech - hook_s - recap_s)
    chosen = _choose(units, tier, content)
    sections: list[Section] = [
        (STEP_OF.get(u.kind, "concept"), f"{u.title}: {u.teach}", s, u.id) for u, s in chosen
    ]
    # A short hook opens the first scene and a short recap closes the last; longer ones are scenes.
    lead, tail = hook_s < MERGE_BELOW_S, recap_s < MERGE_BELOW_S
    if lead:
        step, teach, s, unit = sections[0]
        sections[0] = (step, teach, s + hook_s, unit)
    else:
        hook = "the hook: a question or surprise that makes the learner want the answer"
        sections.insert(0, ("hook", hook, hook_s, None))
    if tail:
        step, teach, s, unit = sections[-1]
        sections[-1] = (step, teach, s + recap_s, unit)
    else:
        sections.append(("recap", LANDING, recap_s, None))
    # The sections fill the speech time: the time asked less the quiet after each scene, which depends
    # on how many scenes they make. A thin plan is stretched by at most MOST_SCALE (a shorter lesson
    # rather than a padded one); scenes are cut after stretching, so they keep their usual length.
    total = sum(s for _, _, s, _ in sections) or 1.0
    factor = 1.0
    for _ in range(4):
        n = len(_scenes_of([(st, t, s * factor, u) for st, t, s, u in sections]))
        better = min(max(10.0, duration_s - n * overhead_s) / total, MOST_SCALE)
        if abs(better - factor) < 0.005:
            break
        factor = better
    scenes = _scenes_of([(st, t, s * factor, u) for st, t, s, u in sections])
    if lead:
        _, teach, s, unit = scenes[0]
        scenes[0] = ("hook", f"open with the hook, then {teach}", s, unit)
    if tail:
        step, teach, s, unit = scenes[-1]
        scenes[-1] = (step, f"{teach}; then {LANDING}", s, unit)
    speech = max(10.0, duration_s - len(scenes) * overhead_s)
    fill = min(1.0, speech / (sum(s for _, _, s, _ in scenes) or 1.0))  # never longer than the time
    planned = tuple(
        PlannedScene(step, teach, max(LEAST_SCENE_WORDS, round(s * fill * wps * AIM)), unit)
        for step, teach, s, unit in scenes
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
    )


def _plain(tier: Tier, duration_s: float, wps: float, overhead_s: float, n: int) -> Blueprint:
    """Without a coverage plan: the depth, the scene count and each scene's budget."""
    speech = max(10.0, duration_s - n * overhead_s)
    each = max(LEAST_SCENE_WORDS, round(speech * wps * AIM / n))
    scenes = [PlannedScene(None, "", each) for _ in range(n)]
    scenes[0] = PlannedScene("hook", "open with the hook", each)
    scenes[-1] = PlannedScene("recap", LANDING, each)
    return Blueprint(tier, duration_s, wps, overhead_s, tuple(scenes))


# ---------------------------------------------------------------------- what the models are told


def length_name(duration_s: float) -> str:
    """'a 30-second', 'a 1-minute', 'a 2.5-minute' (lesson)."""
    if duration_s < 60:
        return f"a {round(duration_s)}-second"
    return f"a {round(duration_s / 60, 1):g}-minute"


def teacher_lines(bp: Blueprint) -> list[str]:
    """The plan for this length, for the teacher's (and the reviewer's) message."""
    lines = [f"Depth: {bp.tier.name}, {length_name(bp.duration_s)} lesson: {bp.tier.brief}"]
    if bp.hook:
        lines.append(f"Hook: {bp.hook}")
    if bp.big_idea:
        lines.append(f"The one idea to land: {bp.big_idea}")
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
    if bp.deeper:
        lines.append(
            "Left for a longer lesson (do not teach these): "
            + "; ".join(u.title for u in bp.deeper[:8])
            + "."
        )
    return lines


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
        head = (
            f"Length: the script has {words} words of narration; the budget is {bp.words}. Use the room "
            "to explain more fully (why, a prediction, a clearer example), up to about that length."
        )
    else:
        head = (
            f"Length: the script has {words} words of narration, close to the budget of {bp.words}. "
            "Keep it so."
        )
    return head + planned + "\nScene words against their budgets: " + "; ".join(counts) + "."


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


def apply_trim(script: TeachingScript, trim: ScriptTrim, allowed: list[int]) -> tuple[TeachingScript, int]:
    """The script with the trimmed scenes' beats in place (only scenes asked for, only when shorter and
    still spoken): the script and how many scenes changed."""
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
        if scene_words(new) >= scene_words(scenes[i]):
            continue
        scenes[i] = new
        changed += 1
    return script.model_copy(update={"scenes": scenes}), changed
