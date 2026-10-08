"""The lesson catalog: what the platform knows about each lesson (owner, sharing, subject, status,
length, cost), kept in step with the pipeline's own records, which it only reads.

- At start: lessons made before accounts (or while the platform was off) are added, owned by the
  first admin (or by nobody until there is one: then only admins see them). What every lesson and
  doubt spent that the usage ledger does not have yet is posted (ADR-053); ratings are recounted and
  what Explore lists is decided again (its rule may have changed).
- While the API runs: lessons being generated are followed from the runner's memory every few
  seconds, their spend posted to their owner (at most every minute, and once more when they stop);
  when one is ready (or stops), its owner gets a notification and Explore is updated.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy import literal, select, update

from animator.platform import curation, usage
from animator.platform.db import chunked
from animator.platform.models import DoubtLog, Lesson, UsageEntry, User

if TYPE_CHECKING:
    from animator.pipeline.models import Generation
    from animator.platform.core import Platform

log = logging.getLogger("animator.platform.catalog")

ACTIVE = ("queued", "planning", "generating", "paused")
SYNC_EVERY_S = 3.0
BILL_EVERY_S = 60.0  # a running lesson's spend is posted at most this often (and when it stops)

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
        self._billed_at: dict[str, float] = {}

    async def run(self, stop: asyncio.Event) -> None:
        await self.startup()
        while not stop.is_set():
            try:
                await self.sync_active()
            except Exception:
                log.exception("catalog sync failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), SYNC_EVERY_S)

    async def startup(self) -> None:
        """Bring the catalog up to date. Each step stands alone: one failing does not stop the others,
        and it runs again at the next start (every step is idempotent)."""
        gens: list[Generation] = []
        runner = self.platform.runner
        if runner is not None:
            try:
                gens = await runner.store.recent(limit=100_000)
            except Exception:
                log.exception("catalog: reading the lessons failed")
        steps: list[tuple[str, Callable[[], Awaitable[Any]]]] = [
            ("adding lessons made before accounts", lambda: self.import_existing(gens)),
            ("posting what lessons spent", lambda: self.reconcile_lessons(gens)),
            ("posting what doubts spent", self.reconcile_doubts),
            ("counting ratings", self.recount_ratings),
            ("deciding what Explore lists", self.refresh_explore),
        ]
        for what, step in steps:
            try:
                await step()
            except Exception:
                log.exception("catalog: %s failed (tried again at the next start)", what)

    async def first_admin(self) -> str | None:
        async with self.platform.db.session() as s:
            return await s.scalar(
                select(User.id)
                .where(User.role == "admin", User.status == "active")
                .order_by(User.created_at)
                .limit(1)
            )

    async def import_existing(self, gens: list[Generation] | None = None) -> int:
        runner = self.platform.runner
        if runner is None:
            return 0
        if gens is None:
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

    def voice_provider(self, language: str) -> str:
        """The voice the pipeline uses for a language (when a lesson's record does not say)."""
        st = self.platform.app_settings
        if st is None:
            return ""
        if language.strip().lower() == "hinglish" and st.animator_tts_provider_hinglish:
            return str(st.animator_tts_provider_hinglish)
        return str(st.animator_tts_provider)

    async def reconcile_lessons(self, gens: list[Generation]) -> int:
        """Post what each lesson has spent and the ledger does not have yet (all of it, the first
        time), dated when the lesson was last worked on. Lessons nobody owns wait for an owner."""
        ps = self.platform.settings
        by_id = {g.id: g for g in gens}
        added = 0
        for chunk in chunked(by_id):
            async with self.platform.db.session() as s:
                rows = (
                    await s.scalars(select(Lesson).where(Lesson.id.in_(chunk), Lesson.owner_id.is_not(None)))
                ).all()
                for row in rows:
                    gen = by_id[row.id]
                    at = gen.updated_at or row.updated_at or row.created_at
                    added += await usage.bill_lesson(s, ps, row, gen, at, self.voice_provider(row.language))
                await s.commit()
        if added:
            log.info("usage: %d entr%s posted for lessons", added, "y" if added == 1 else "ies")
        return added

    async def reconcile_doubts(self) -> int:
        """Doubt answers asked before the ledger existed are posted once (with their voice, from the
        doubt's own record when it is still there)."""
        ps = self.platform.settings
        key = literal("doubt:") + DoubtLog.id + literal(":ai")
        async with self.platform.db.session() as s:
            missing = (
                await s.scalars(
                    select(DoubtLog)
                    .outerjoin(UsageEntry, UsageEntry.source == key)
                    .where(UsageEntry.id.is_(None))
                    .order_by(DoubtLog.created_at)
                    .limit(50_000)
                )
            ).all()
            added = 0
            for d in missing:
                turn = await asyncio.to_thread(self._doubt_turn, d.lesson_id, d.session_id, d.turn)
                if turn is None:
                    turn = {"cost_usd": d.cost_usd}
                added += await usage.bill_doubt(s, ps, d.id, d.user_id, d.lesson_id, turn, d.created_at)
            await s.commit()
        if added:
            log.info("usage: %d entr%s posted for doubts", added, "y" if added == 1 else "ies")
        return added

    def _doubt_turn(self, lesson_id: str, session_id: str, n: int) -> dict[str, Any] | None:
        doubts = self.platform.doubts
        if doubts is None or not session_id:
            return None
        try:
            session = doubts.sessions.load(lesson_id, session_id)
        except Exception:
            return None
        for turn in (session or {}).get("turns") or []:
            if isinstance(turn, dict) and int(turn.get("n") or 0) == n:
                return turn
        return None

    async def recount_ratings(self) -> int:
        async with self.platform.db.session() as s:
            changed = await curation.recount(s)
            await s.commit()
        if changed:
            log.info("ratings: %d lesson(s) recounted", changed)
        return changed

    async def refresh_explore(self) -> int:
        async with self.platform.db.session() as s:
            listed = await curation.refresh_all(s, self.platform.settings)
            await s.commit()
        await curation.announce(self.platform, listed)
        return len(listed)

    async def claim_orphans(self, admin_id: str) -> int:
        """Lessons nobody owns (made before accounts) go to the first admin, with what they spent
        (posted now: a lesson's spend waits for an owner)."""
        ps = self.platform.settings
        runner = self.platform.runner
        listed: list[curation.Listed] = []
        async with self.platform.db.session() as s:
            ids = list(
                (
                    await s.scalars(
                        select(Lesson.id).where(Lesson.owner_id.is_(None), Lesson.deleted_at.is_(None))
                    )
                ).all()
            )
            for chunk in chunked(ids):
                await s.execute(
                    update(Lesson)
                    .where(Lesson.id.in_(chunk), Lesson.owner_id.is_(None))
                    .values(owner_id=admin_id)
                )
            if ids:
                await curation.recount(s, ids)  # an owner's own ratings do not count
                listed = await curation.refresh(s, ps, ids)
            await s.commit()
            if ids and runner is not None:
                for chunk in chunked(ids):
                    rows = (await s.scalars(select(Lesson).where(Lesson.id.in_(chunk)))).all()
                    for row in rows:
                        gen = await runner.get(row.id)
                        if gen is not None:
                            at = gen.updated_at or row.created_at
                            await usage.bill_lesson(s, ps, row, gen, at, self.voice_provider(row.language))
                    await s.commit()
        if ids:
            log.info("catalog: %d lesson(s) without an owner now belong to the first admin", len(ids))
            await curation.announce(self.platform, listed)
        return len(ids)

    async def sync_active(self) -> None:
        runner = self.platform.runner
        if runner is None:
            return
        ps = self.platform.settings
        now = time.time()
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
            changed: list[str] = []
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
                status_changed = apply(row, gen)
                if row.owner_id is not None and (
                    not running or now - self._billed_at.get(row.id, 0.0) >= BILL_EVERY_S
                ):
                    await usage.bill_lesson(s, ps, row, gen, now, self.voice_provider(row.language))
                    if running:
                        self._billed_at[row.id] = now
                if not running:
                    self._billed_at.pop(row.id, None)
                if status_changed:
                    changed.append(row.id)
                if status_changed and row.owner_id:
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
            # Ready (or stopped): a published or well rated lesson enters (or leaves) Explore.
            listed = await curation.refresh(s, ps, changed, now) if changed else []
            await s.commit()
        for user_id, kind, title, link in notices:
            body = (
                "Open it to watch, share or ask doubts." if kind == "lesson_ready" else "Open it to resume."
            )
            await self.platform.notify(user_id, kind, title, body, link)
        await curation.announce(self.platform, listed)

    def follow(self, lesson_id: str) -> None:
        self._watched.add(lesson_id)
