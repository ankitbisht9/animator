"""The lesson catalog: what the platform knows about each lesson (owner, sharing, subject, status,
length, cost), kept in step with the pipeline's own records, which it only reads.

- At start: lessons made before accounts (or while the platform was off) are added, owned by the
  first admin (or by nobody until there is one: then only admins see them).
- While the API runs: lessons being generated are followed from the runner's memory every few
  seconds; when one is ready (or stops), its owner gets a notification.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from animator.platform.models import Lesson, User

if TYPE_CHECKING:
    from animator.pipeline.models import Generation
    from animator.platform.core import Platform

log = logging.getLogger("animator.platform.catalog")

ACTIVE = ("queued", "planning", "generating", "paused")
SYNC_EVERY_S = 3.0

SUBJECTS: dict[str, str] = {
    "physics": "Physics",
    "chemistry": "Chemistry",
    "biology": "Biology",
    "mathematics": "Mathematics",
    "computer-science": "Computer Science",
    "earth-space": "Earth & Space",
    "economics": "Economics",
    "history": "History & Society",
    "language-arts": "Language & Arts",
    "general": "General",
}

_KEYWORDS: dict[str, str] = {
    "physics": (
        "force forces motion velocity speed acceleration gravity gravitational energy momentum "
        "pendulum wave waves light sound electric electricity magnet magnetic circuit current voltage "
        "pressure buoyancy archimedes newton friction heat thermodynamics optics lens refraction "
        "reflection quantum relativity displacement distance projectile torque inertia density "
        "kinetic potential power work spring oscillation frequency resistance ohm lever pulley float "
        "sink train trains track tracks rail rails wheel wheels flight fly airplane rocket rockets "
        "engine magnetism"
    ),
    "chemistry": (
        "atom atoms molecule molecules reaction reactions acid acids base bases ph periodic element "
        "elements compound compounds bond bonds bonding oxidation reduction chemical chemistry mole "
        "stoichiometry salt catalyst organic carbon ion ions isotope electrolysis solution solubility"
    ),
    "biology": (
        "cell cells photosynthesis dna rna gene genes genetics evolution organism plant plants animal "
        "animals heart blood digestion enzyme enzymes protein proteins virus viruses bacteria "
        "ecosystem respiration mitosis meiosis neuron neurons brain immune vaccine body organ organs "
        "chlorophyll fermentation yeast grapes wine"
    ),
    "mathematics": (
        "theorem pythagorean pythagoras equation equations algebra geometry triangle triangles circle "
        "fraction fractions calculus derivative derivatives integral integrals probability statistics "
        "matrix matrices function functions graph prime primes trigonometry sine cosine tangent area "
        "volume angle angles ratio percentage logarithm logarithms number numbers sequence series "
        "vector vectors multiplication division addition subtraction"
    ),
    "computer-science": (
        "algorithm algorithms binary search sort sorting programming code coding data structure "
        "structures recursion network networks internet computer computers cpu memory machine "
        "learning neural database encryption compiler software hashing hash queue stack tree graphs "
        "rag llm llms langchain langgraph blockchain ai gpt chatgpt transformer transformers "
        "embedding embeddings retrieval agent agents api apis python javascript crypto bitcoin cloud "
        "cybersecurity"
    ),
    "earth-space": (
        "planet planets solar earth moon star stars galaxy universe climate weather volcano volcanoes "
        "earthquake earthquakes tectonic ocean oceans season seasons eclipse orbit orbits atmosphere "
        "rain cloud clouds rock rocks tide tides space black hole"
    ),
    "economics": (
        "economy economics inflation demand supply market markets money interest bank banks tax taxes "
        "gdp trade price prices investment stock stocks budget profit gst upi loan loans"
    ),
    "history": (
        "history historical war wars empire revolution civilization ancient king queen independence "
        "constitution democracy government election society culture"
    ),
    "language-arts": (
        "grammar poem poetry literature writing music art painting language languages essay story "
        "stories novel vocabulary"
    ),
}
_WORDS = {subject: set(words.split()) for subject, words in _KEYWORDS.items()}


# Everyday words that also name a concept count for less than words only one subject uses.
_WEAK = frozenset(
    {
        "work",
        "power",
        "body",
        "solution",
        "data",
        "space",
        "number",
        "numbers",
        "light",
        "current",
        "base",
        "bases",
        "series",
        "function",
        "functions",
        "graph",
        "graphs",
        "culture",
        "society",
        "story",
        "stories",
        "art",
        "interest",
        "rock",
        "rocks",
        "distance",
        "area",
        "volume",
        "tree",
        "stack",
        "queue",
        "market",
        "price",
        "prices",
        "code",
    }
)


def subject_of(topic: str) -> str:
    """The subject a topic most likely belongs to (keywords, no model call); `general` when unsure."""
    words = re.findall(r"[a-z]+", topic.lower())
    best, score = "general", 0.0
    for subject, keys in _WORDS.items():
        n = sum(0.4 if w in _WEAK else 1.0 for w in words if w in keys)
        if n > score:
            best, score = subject, n
    return best


def length_s(gen: Generation) -> float:
    if gen.scenes:
        return round(sum(s.duration if s.duration is not None else s.brief.duration_s for s in gen.scenes), 1)
    return round(gen.plan.target_duration_s if gen.plan else gen.request.duration_min * 60, 1)


def apply(row: Lesson, gen: Generation) -> bool:
    """Copy what the platform shows from the lesson's record. True when the status changed."""
    changed = row.status != gen.status
    row.status = gen.status
    if gen.plan:
        row.title = gen.plan.title[:200]
        row.subtitle = (gen.plan.subtitle or "")[:400]
    elif not row.title:
        row.title = gen.request.topic[:200]
    row.scene_count = len(gen.scenes)
    row.planned_s = length_s(gen)
    row.cost_usd = round(gen.metrics.cost_usd, 6)
    row.updated_at = gen.updated_at
    if gen.status == "complete" and row.completed_at is None:
        row.completed_at = gen.updated_at
    return changed


def new_row(gen: Generation, owner_id: str | None) -> Lesson:
    r = gen.request
    row = Lesson(
        id=gen.id,
        owner_id=owner_id,
        topic=r.topic[:500],
        title=r.topic[:200],
        audience=r.audience[:120],
        language=(r.language or "English")[:40],
        duration_min=float(r.duration_min),
        subject=subject_of(r.topic),
        visibility="private",
        created_at=gen.created_at,
    )
    apply(row, gen)
    return row


class Catalog:
    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self._watched: set[str] = set()

    async def run(self, stop: asyncio.Event) -> None:
        try:
            await self.import_existing()
        except Exception:
            log.exception("importing existing lessons failed (they appear after the next restart)")
        while not stop.is_set():
            try:
                await self.sync_active()
            except Exception:
                log.exception("catalog sync failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), SYNC_EVERY_S)

    async def first_admin(self) -> str | None:
        async with self.platform.db.session() as s:
            return await s.scalar(
                select(User.id)
                .where(User.role == "admin", User.status == "active")
                .order_by(User.created_at)
                .limit(1)
            )

    async def import_existing(self) -> int:
        runner = self.platform.runner
        if runner is None:
            return 0
        gens = await runner.store.recent(limit=100_000)
        async with self.platform.db.session() as s:
            known = set((await s.scalars(select(Lesson.id))).all())
        missing = [g for g in gens if g.id not in known]
        if not missing:
            return 0
        owner = await self.first_admin()
        async with self.platform.db.session() as s:
            for g in missing:
                s.add(new_row(g, owner))
            await s.commit()
        log.info("catalog: %d existing lesson(s) added%s", len(missing), "" if owner else " (no admin yet)")
        return len(missing)

    async def claim_orphans(self, admin_id: str) -> int:
        """Lessons nobody owns (made before accounts) go to the first admin."""
        async with self.platform.db.session() as s:
            result = await s.execute(
                update(Lesson)
                .where(Lesson.owner_id.is_(None), Lesson.deleted_at.is_(None))
                .values(owner_id=admin_id)
            )
            await s.commit()
        n = int(getattr(result, "rowcount", 0) or 0)
        if n:
            log.info("catalog: %d lesson(s) without an owner now belong to the first admin", n)
        return n

    async def sync_active(self) -> None:
        runner = self.platform.runner
        if runner is None:
            return
        async with self.platform.db.session() as s:
            rows = list(
                (
                    await s.scalars(
                        select(Lesson).where(Lesson.status.in_(ACTIVE), Lesson.deleted_at.is_(None))
                    )
                ).all()
            )
            # Lessons that just finished stay followed once more, for their final status and cost.
            finishing = set(self._watched) - {r.id for r in rows}
            if finishing:
                rows += list((await s.scalars(select(Lesson).where(Lesson.id.in_(finishing)))).all())
            notices: list[tuple[str, str, str, str]] = []
            for row in rows:
                running = runner.running(row.id)
                if not running and row.id not in self._watched:
                    continue
                gen = await runner.get(row.id)
                if gen is None:
                    self._watched.discard(row.id)
                    continue
                if running:
                    self._watched.add(row.id)
                else:
                    self._watched.discard(row.id)
                if apply(row, gen) and row.owner_id:
                    if gen.status == "complete":
                        notices.append(
                            (
                                row.owner_id,
                                "lesson_ready",
                                f"Your lesson is ready: {row.title}",
                                f"/lessons/{row.id}",
                            )
                        )
                    elif gen.status == "failed":
                        notices.append(
                            (
                                row.owner_id,
                                "lesson_failed",
                                f"Your lesson stopped: {row.title}",
                                f"/lessons/{row.id}",
                            )
                        )
            await s.commit()
        for user_id, kind, title, link in notices:
            body = (
                "Open it to watch, share or ask doubts." if kind == "lesson_ready" else "Open it to resume."
            )
            await self.platform.notify(user_id, kind, title, body, link)

    def follow(self, lesson_id: str) -> None:
        self._watched.add(lesson_id)
