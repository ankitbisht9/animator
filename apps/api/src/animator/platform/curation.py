"""Ratings and what Explore lists (ADR-053).

Whether a lesson is in Explore is decided in one place, `decide`, from:
- the lesson: ready and not removed; private is never listed; public means its owner published it;
- its ratings: a lesson shared with a link is listed by itself once at least `explore_min_raters`
  people rated it `explore_min_average` or more on average, unless its owner opted out;
- an admin's pin: include (whatever its ratings say) or exclude (whatever else says);
- its owner: a suspended owner's lessons leave Explore unless an admin included them.

The decision is stored on the lesson (`explore_listed`, `explore_source`), so Explore stays one indexed
query. It is refreshed whenever one of its inputs changes (a rating, sharing, a pin, an owner's
suspension, the lesson finishing or being removed) and for every candidate at start, since the rule's
settings may have changed.

Ratings that count (for the rule and for the average shown) are by active accounts other than the
lesson's owner. `Lesson.rating_sum` and `rating_count` are recounted from the ratings, never added to,
so they cannot drift.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.orm.util import identity_key

from animator.platform.db import chunked
from animator.platform.models import Curation, Lesson, Rating, User
from animator.platform.settings import PlatformSettings

if TYPE_CHECKING:
    from animator.platform.core import Platform

PINS = ("include", "exclude")
REVIEWS = ("flagged", "working", "resolved")
PRIOR_WEIGHT = 10  # the weighted score trusts a lesson's own ratings over the platform's mean after ~10
DEFAULT_MEAN = 3.5

# The rated lesson, joined to a rating (an alias: queries may also select `Lesson` itself).
rated_lesson = aliased(Lesson, name="rated_lesson")


def counted() -> Any:
    """Ratings that count: by an active account that is not the lesson's owner. Needs the rater
    (`User`) and the rated lesson (`rated_lesson`) joined."""
    return and_(
        User.status == "active", or_(rated_lesson.owner_id.is_(None), Rating.user_id != rated_lesson.owner_id)
    )


def qualifies(rating_sum: int, rating_count: int, settings: PlatformSettings) -> bool:
    """The rule, exactly: enough people, and an average at or above the bar (integer arithmetic, so
    4.0 means 4.0)."""
    if rating_count < settings.explore_min_raters or rating_count <= 0:
        return False
    return rating_sum * 100 >= round(settings.explore_min_average * 100) * rating_count


@dataclass(frozen=True)
class Decision:
    listed: bool
    source: str  # owner | auto | admin, or "" when not listed
    reason: str  # why, for the people who curate (see REASONS)


REASONS: dict[str, str] = {
    "removed": "The lesson was removed.",
    "not_ready": "The lesson is not ready.",
    "private": "The lesson is private.",
    "excluded": "An admin keeps it out of Explore.",
    "included": "An admin added it to Explore.",
    "owner_suspended": "Its owner's account is suspended.",
    "published": "Its owner published it.",
    "auto_off": "Listing by ratings is turned off.",
    "opted_out": "Its owner chose not to be listed by ratings.",
    "rated": "Learners rated it highly enough.",
    "below_rule": "Not rated highly enough by enough people yet.",
}


def decide(
    *,
    status: str,
    deleted: bool,
    visibility: str,
    opt_out: bool,
    rating_sum: int,
    rating_count: int,
    pin: str,
    owner_active: bool,
    settings: PlatformSettings,
) -> Decision:
    if deleted:
        return Decision(False, "", "removed")
    if status != "complete":
        return Decision(False, "", "not_ready")
    if visibility == "private":
        return Decision(False, "", "private")
    if pin == "exclude":
        return Decision(False, "", "excluded")
    if pin == "include":
        return Decision(True, "admin", "included")
    if not owner_active:
        return Decision(False, "", "owner_suspended")
    if visibility == "public":
        return Decision(True, "owner", "published")
    if not settings.explore_auto:
        return Decision(False, "", "auto_off")
    if opt_out:
        return Decision(False, "", "opted_out")
    if qualifies(rating_sum, rating_count, settings):
        return Decision(True, "auto", "rated")
    return Decision(False, "", "below_rule")


def decide_row(
    row: Lesson, pin: str | None, owner_status: str | None, settings: PlatformSettings
) -> Decision:
    return decide(
        status=row.status,
        deleted=row.deleted_at is not None,
        visibility=row.visibility,
        opt_out=row.explore_opt_out,
        rating_sum=row.rating_sum,
        rating_count=row.rating_count,
        pin=pin or "",
        owner_active=owner_status in (None, "active"),
        settings=settings,
    )


@dataclass(frozen=True)
class Listed:
    """A lesson that entered Explore for the first time (its owner is told)."""

    lesson_id: str
    owner_id: str | None
    title: str
    source: str
    rating: float | None


def _store(row: Lesson, d: Decision, now: float) -> bool:
    """Write a decision on the lesson. True when it is listed and never was before (the caller claims
    that first listing, so its owner is told once)."""
    if d.listed:
        if not row.explore_listed:
            row.explore_listed = True
            row.explore_at = now
        if row.explore_source != d.source:
            row.explore_source = d.source
        return row.explore_first_at is None
    if row.explore_listed or row.explore_source or row.explore_at is not None:
        row.explore_listed = False
        row.explore_source = ""
        row.explore_at = None
    return False


async def _first_listing(s: AsyncSession, row: Lesson, now: float) -> bool:
    """Mark a lesson's first listing, by compare-and-set: of two deciders, one tells the owner."""
    result = await s.execute(
        update(Lesson)
        .where(Lesson.id == row.id, Lesson.explore_first_at.is_(None))
        .values(explore_first_at=now)
        .execution_options(synchronize_session=False)
    )
    if int(getattr(result, "rowcount", 0) or 0) != 1:
        await s.refresh(row, ["explore_first_at"])
        return False
    set_committed_value(row, "explore_first_at", now)
    return True


async def refresh(
    s: AsyncSession, settings: PlatformSettings, lesson_ids: Iterable[str], now: float | None = None
) -> list[Listed]:
    """Decide again for these lessons and store it (in the caller's transaction). Returns the
    lessons listed for the first time, for `announce` after the commit."""
    now = time.time() if now is None else now
    out: list[Listed] = []
    for chunk in chunked(lesson_ids):
        rows = (
            await s.execute(
                select(Lesson, Curation.pin, User.status)
                .outerjoin(Curation, Curation.lesson_id == Lesson.id)
                .outerjoin(User, User.id == Lesson.owner_id)
                .where(Lesson.id.in_(chunk))
            )
        ).all()
        for row, pin, owner_status in rows:
            if _store(row, decide_row(row, pin, owner_status, settings), now) and await _first_listing(
                s, row, now
            ):
                out.append(
                    Listed(
                        row.id,
                        row.owner_id,
                        row.title or row.topic,
                        row.explore_source,
                        round(row.rating_sum / row.rating_count, 1) if row.rating_count else None,
                    )
                )
    return out


async def refresh_all(s: AsyncSession, settings: PlatformSettings) -> list[Listed]:
    """Every lesson whose listing could be anything but 'no': listed now, public, pinned, or rated by
    enough people. The rest cannot be listed, and are not, so they are left alone."""
    ids = (
        await s.scalars(
            select(Lesson.id)
            .outerjoin(Curation, Curation.lesson_id == Lesson.id)
            .where(
                or_(
                    Lesson.explore_listed.is_(True),
                    Lesson.explore_source != "",
                    Lesson.visibility == "public",
                    Lesson.rating_count >= settings.explore_min_raters,
                    Curation.pin != "",
                )
            )
        )
    ).all()
    return await refresh(s, settings, ids)


async def recount(s: AsyncSession, lesson_ids: Iterable[str] | None = None) -> int:
    """Set each lesson's rating sum and count from the ratings that count. None: every lesson (only
    those whose numbers differ are written). Returns how many changed."""
    changed = 0
    batches: Iterable[list[str] | None] = [None] if lesson_ids is None else chunked(lesson_ids)
    for chunk in batches:
        totals = (
            select(
                Rating.lesson_id.label("lid"),
                func.count().label("n"),
                func.sum(Rating.stars).label("total"),
            )
            .join(User, User.id == Rating.user_id)
            .join(rated_lesson, rated_lesson.id == Rating.lesson_id)
            .where(counted())
            .group_by(Rating.lesson_id)
        )
        if chunk is not None:
            totals = totals.where(Rating.lesson_id.in_(chunk))
        sub = totals.subquery()
        n = func.coalesce(sub.c.n, 0)
        total = func.coalesce(sub.c.total, 0)
        stale = (
            select(Lesson.id, n, total)
            .outerjoin(sub, sub.c.lid == Lesson.id)
            .where(or_(Lesson.rating_count != n, Lesson.rating_sum != total))
        )
        if chunk is not None:
            stale = stale.where(Lesson.id.in_(chunk))
        for lesson_id, count, stars in (await s.execute(stale)).all():
            row = s.identity_map.get(identity_key(Lesson, lesson_id))
            if isinstance(row, Lesson):  # loaded in this session: keep the object in step
                row.rating_count, row.rating_sum = int(count), int(stars)
            else:
                await s.execute(
                    update(Lesson)
                    .where(Lesson.id == lesson_id)
                    .values(rating_count=int(count), rating_sum=int(stars))
                )
            changed += 1
    return changed


async def announce(platform: Platform, listed: list[Listed]) -> None:
    """Tell owners their lesson is in Explore (only the first time; publishing it oneself needs no note)."""
    for e in listed:
        if e.owner_id is None or e.source == "owner":
            continue
        if e.source == "auto":
            body = (
                f"Learners rated it {e.rating:.1f} out of 5, so it now appears in Explore under its subject."
                if e.rating is not None
                else "Learners rated it highly, so it now appears in Explore."
            )
        else:
            body = "An administrator added it to Explore, under its subject."
        await platform.notify(
            e.owner_id, "explore", f"Your lesson is in Explore: {e.title}", body, f"/lessons/{e.lesson_id}"
        )


def weighted(rating_sum: int, rating_count: int, mean: float) -> float:
    """A rating that needs evidence: a lesson's average pulled towards the platform's mean until it
    has about PRIOR_WEIGHT ratings (so one 5-star rating does not top the list)."""
    return (PRIOR_WEIGHT * mean + rating_sum) / (PRIOR_WEIGHT + rating_count)


async def platform_mean(s: AsyncSession) -> float:
    row = (
        await s.execute(
            select(
                func.coalesce(func.sum(Lesson.rating_sum), 0), func.coalesce(func.sum(Lesson.rating_count), 0)
            ).where(Lesson.deleted_at.is_(None))
        )
    ).one()
    total, n = int(row[0] or 0), int(row[1] or 0)
    return total / n if n else DEFAULT_MEAN


async def distribution(s: AsyncSession, lesson_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Per lesson: how many counted ratings gave 1..5 stars, and when it was last rated."""
    out: dict[str, dict[str, Any]] = {}
    for chunk in chunked(lesson_ids):
        rows = (
            await s.execute(
                select(Rating.lesson_id, Rating.stars, func.count(), func.max(Rating.updated_at))
                .join(User, User.id == Rating.user_id)
                .join(rated_lesson, rated_lesson.id == Rating.lesson_id)
                .where(counted(), Rating.lesson_id.in_(chunk))
                .group_by(Rating.lesson_id, Rating.stars)
            )
        ).all()
        for lesson_id, stars, n, last in rows:
            cell = out.setdefault(lesson_id, {"stars": [0, 0, 0, 0, 0], "last_rated_at": None})
            if 1 <= int(stars) <= 5:
                cell["stars"][int(stars) - 1] += int(n)
            if last is not None and (cell["last_rated_at"] is None or last > cell["last_rated_at"]):
                cell["last_rated_at"] = float(last)
    return out
