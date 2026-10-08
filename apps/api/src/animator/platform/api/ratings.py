"""/api/v1/admin/ratings and /api/v1/admin/explore: how lessons are rated, and what Explore lists
(ADR-053).

- Monitoring (`ratings:read`): the platform's ratings at a glance; lessons by view (top, poorly rated,
  meeting the Explore rule, almost there, in Explore, kept out, under review) with their average, a
  weighted score, the stars' spread and why each is or is not in Explore; one lesson's ratings and
  comments.
- Curating (`explore:curate`): add a lesson to Explore whatever its ratings, keep one out whatever
  else says, or hand it back to the rule; optionally file it under another subject.
- Review (`review:manage`): flag a lesson for the team to improve, mark the work under way or done.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from animator.platform import curation
from animator.platform.catalog import SUBJECTS
from animator.platform.curation import counted, rated_lesson
from animator.platform.deps import DB, Curator, Plat, RatingsReader, Reviewer, client_ip
from animator.platform.models import AuditEvent, Curation, Lesson, Rating, User
from animator.platform.schemas import ExploreIn, ReviewIn
from animator.platform.settings import PlatformSettings

router = APIRouter(prefix="/api/v1/admin", tags=["admin: ratings and Explore"])

# all: every lesson (to add one to Explore whatever its ratings); the others are about ratings.
View = Literal["all", "rated", "top", "poor", "rule", "near", "listed", "excluded", "review"]
Sort = Literal["score", "worst", "average", "count", "recent", "new"]


def _rule_met(ps: PlatformSettings) -> Any:
    bar = round(ps.explore_min_average * 100)
    return and_(
        Lesson.rating_count >= ps.explore_min_raters, Lesson.rating_sum * 100 >= bar * Lesson.rating_count
    )


def _item(
    row: Lesson,
    owner_name: str | None,
    owner_status: str | None,
    cur: Curation | None,
    spread: dict[str, Any] | None,
    mean: float,
    ps: PlatformSettings,
) -> dict[str, Any]:
    pin = cur.pin if cur else ""
    decision = curation.decide_row(row, pin, owner_status, ps)
    n = row.rating_count
    return {
        "id": row.id,
        "title": row.title or row.topic,
        "subject": row.subject,
        "subject_label": SUBJECTS.get(row.subject, "General"),
        "status": row.status,
        "visibility": row.visibility,
        "featured": row.featured,
        "views": row.views,
        "created_at": row.created_at,
        "owner": {"id": row.owner_id, "name": owner_name, "status": owner_status} if row.owner_id else None,
        "rating": {
            "average": round(row.rating_sum / n, 2) if n else None,
            "count": n,
            "score": round(curation.weighted(row.rating_sum, n, mean), 3),
            "stars": (spread or {}).get("stars", [0, 0, 0, 0, 0]),
            "last_rated_at": (spread or {}).get("last_rated_at"),
            "meets_rule": curation.qualifies(row.rating_sum, n, ps),
        },
        "explore": {
            "listed": row.explore_listed,
            "source": row.explore_source,
            "since": row.explore_at,
            "pin": pin,
            "pin_note": cur.pin_note if cur else "",
            "pinned_at": cur.pinned_at if cur else None,
            "opt_out": row.explore_opt_out,
            "reason": decision.reason,
            "reason_text": curation.REASONS.get(decision.reason, ""),
        },
        "review": {
            "status": cur.review if cur else "",
            "note": cur.review_note if cur else "",
            "at": cur.reviewed_at if cur else None,
        },
    }


def _rule_out(ps: PlatformSettings) -> dict[str, Any]:
    return {
        "auto": ps.explore_auto,
        "min_raters": ps.explore_min_raters,
        "min_average": ps.explore_min_average,
    }


@router.get("/ratings/summary")
async def summary(p: RatingsReader, platform: Plat, s: DB) -> dict[str, Any]:
    ps = platform.settings
    live = (Lesson.deleted_at.is_(None),)
    rated = await s.scalar(select(func.count()).select_from(Lesson).where(*live, Lesson.rating_count > 0))
    mean = await curation.platform_mean(s)
    spread = [0, 0, 0, 0, 0]
    for stars, n in (
        await s.execute(
            select(Rating.stars, func.count())
            .join(User, User.id == Rating.user_id)
            .join(rated_lesson, rated_lesson.id == Rating.lesson_id)
            .where(counted(), rated_lesson.deleted_at.is_(None))
            .group_by(Rating.stars)
        )
    ).all():
        if 1 <= int(stars) <= 5:
            spread[int(stars) - 1] = int(n)
    listed = dict(
        (
            await s.execute(
                select(Lesson.explore_source, func.count())
                .where(*live, Lesson.explore_listed.is_(True))
                .group_by(Lesson.explore_source)
            )
        ).all()
    )
    bar = round(ps.explore_min_average * 100)

    async def count(*where: Any) -> int:
        return int(await s.scalar(select(func.count()).select_from(Lesson).where(*live, *where)) or 0)

    reviews = dict(
        (
            await s.execute(
                select(Curation.review, func.count())
                .join(Lesson, Lesson.id == Curation.lesson_id)
                .where(*live, Curation.review != "")
                .group_by(Curation.review)
            )
        ).all()
    )
    comments = (
        await s.execute(
            select(Rating, User.name, rated_lesson.title, rated_lesson.topic)
            .join(User, User.id == Rating.user_id)
            .join(rated_lesson, rated_lesson.id == Rating.lesson_id)
            .where(counted(), rated_lesson.deleted_at.is_(None), Rating.comment != "")
            .order_by(Rating.updated_at.desc())
            .limit(8)
        )
    ).all()
    return {
        "rule": _rule_out(ps),
        "rated_lessons": int(rated or 0),
        "ratings": sum(spread),
        "average": round(sum((i + 1) * n for i, n in enumerate(spread)) / sum(spread), 2)
        if sum(spread)
        else None,
        "platform_mean": round(mean, 3),
        "stars": spread,
        "in_explore": {
            "total": sum(int(v) for v in listed.values()),
            "owner": int(listed.get("owner", 0)),
            "auto": int(listed.get("auto", 0)),
            "admin": int(listed.get("admin", 0)),
        },
        "meets_rule": await count(_rule_met(ps), Lesson.status == "complete"),
        "near_rule": await count(
            Lesson.rating_count > 0,
            Lesson.rating_count < ps.explore_min_raters,
            Lesson.rating_sum * 100 >= bar * Lesson.rating_count,
        ),
        "poor": await count(Lesson.rating_count > 0, Lesson.rating_sum * 100 < 300 * Lesson.rating_count),
        "excluded": int(
            await s.scalar(
                select(func.count())
                .select_from(Curation)
                .join(Lesson, Lesson.id == Curation.lesson_id)
                .where(*live, Curation.pin == "exclude")
            )
            or 0
        ),
        "review": {k: int(reviews.get(k, 0)) for k in curation.REVIEWS},
        "recent_comments": [
            {
                "lesson_id": r.lesson_id,
                "lesson_title": title or topic,
                "name": name,
                "stars": r.stars,
                "comment": r.comment,
                "at": r.updated_at,
            }
            for r, name, title, topic in comments
        ],
    }


@router.get("/ratings/lessons")
async def rated_lessons(
    p: RatingsReader,
    platform: Plat,
    s: DB,
    view: View = "rated",
    subject: str | None = None,
    q: str | None = None,
    sort: Sort | None = None,
    below: float = 3.0,
    offset: int = 0,
    limit: int = 25,
) -> dict[str, Any]:
    ps = platform.settings
    limit = max(1, min(limit, 100))
    below = max(1.0, min(5.0, below))
    mean = await curation.platform_mean(s)
    w = curation.PRIOR_WEIGHT
    score = (w * mean + Lesson.rating_sum) * 1.0 / (w + Lesson.rating_count)
    average = Lesson.rating_sum * 1.0 / func.nullif(Lesson.rating_count, 0)
    last = (
        select(Rating.lesson_id.label("lid"), func.max(Rating.updated_at).label("at"))
        .group_by(Rating.lesson_id)
        .subquery()
    )
    stmt = (
        select(Lesson, User.name, User.status, Curation)
        .outerjoin(User, User.id == Lesson.owner_id)
        .outerjoin(Curation, Curation.lesson_id == Lesson.id)
        .outerjoin(last, last.c.lid == Lesson.id)
        .where(Lesson.deleted_at.is_(None))
    )
    bar = round(ps.explore_min_average * 100)
    if view in ("rated", "top"):
        stmt = stmt.where(Lesson.rating_count > 0)
    elif view == "poor":
        stmt = stmt.where(
            Lesson.rating_count > 0, Lesson.rating_sum * 100 < round(below * 100) * Lesson.rating_count
        )
    elif view == "rule":
        stmt = stmt.where(_rule_met(ps), Lesson.status == "complete")
    elif view == "near":
        stmt = stmt.where(
            Lesson.rating_count > 0,
            Lesson.rating_count < ps.explore_min_raters,
            Lesson.rating_sum * 100 >= bar * Lesson.rating_count,
        )
    elif view == "listed":
        stmt = stmt.where(Lesson.explore_listed.is_(True))
    elif view == "excluded":
        stmt = stmt.where(Curation.pin == "exclude")
    elif view == "review":
        stmt = stmt.where(Curation.review != "")
    if subject and subject in SUBJECTS:
        stmt = stmt.where(Lesson.subject == subject)
    if q and q.strip():
        term = f"%{q.strip()[:80].lower()}%"
        stmt = stmt.where(or_(func.lower(Lesson.title).like(term), func.lower(Lesson.topic).like(term)))
    total = await s.scalar(select(func.count()).select_from(stmt.with_only_columns(Lesson.id).subquery()))
    chosen = sort or {"all": "new", "poor": "worst", "review": "recent", "listed": "recent"}.get(
        view, "score"
    )
    # "recent": the latest review for the review view, the latest listing for Explore, else the latest rating.
    recent: Any = {"review": Curation.reviewed_at, "listed": Lesson.explore_at}.get(view, last.c.at)
    orders: dict[str, list[Any]] = {
        "score": [score.desc(), Lesson.rating_count.desc()],
        "worst": [score.asc(), Lesson.rating_count.desc()],
        "average": [func.coalesce(average, 0).desc(), Lesson.rating_count.desc()],
        "count": [Lesson.rating_count.desc()],
        "new": [Lesson.created_at.desc()],
        "recent": [func.coalesce(recent, 0).desc()],
    }
    order = orders[chosen]
    rows = (
        await s.execute(stmt.order_by(*order, Lesson.created_at.desc()).offset(max(0, offset)).limit(limit))
    ).all()
    spread = await curation.distribution(s, [r[0].id for r in rows])
    return {
        "total": int(total or 0),
        "view": view,
        "sort": chosen,
        "rule": _rule_out(ps),
        "platform_mean": round(mean, 3),
        "items": [
            _item(row, name, status, cur, spread.get(row.id), mean, ps) for row, name, status, cur in rows
        ],
    }


async def _one(s: AsyncSession, lesson_id: str) -> tuple[Lesson, str | None, str | None, Curation | None]:
    found = (
        await s.execute(
            select(Lesson, User.name, User.status, Curation)
            .outerjoin(User, User.id == Lesson.owner_id)
            .outerjoin(Curation, Curation.lesson_id == Lesson.id)
            .where(Lesson.id == lesson_id)
        )
    ).first()
    if found is None:
        raise HTTPException(404, "No such lesson.")
    row, name, status, cur = found
    return row, name, status, cur


@router.get("/ratings/lessons/{lesson_id}")
async def lesson_ratings(
    lesson_id: str,
    p: RatingsReader,
    platform: Plat,
    s: DB,
    stars: int | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """A lesson's ratings and comments (newest first), whether each counts, and its Explore history."""
    ps = platform.settings
    row, owner_name, owner_status, cur = await _one(s, lesson_id)
    mean = await curation.platform_mean(s)
    spread = await curation.distribution(s, [row.id])
    stmt = (
        select(Rating, User.name, User.status)
        .join(User, User.id == Rating.user_id)
        .where(Rating.lesson_id == row.id)
    )
    if stars is not None and 1 <= stars <= 5:
        stmt = stmt.where(Rating.stars == stars)
    total = await s.scalar(
        select(func.count()).select_from(stmt.with_only_columns(Rating.user_id).subquery())
    )
    ratings = (
        await s.execute(
            stmt.order_by(Rating.updated_at.desc()).offset(max(0, offset)).limit(max(1, min(limit, 200)))
        )
    ).all()
    actor = User.__table__.alias("actor")
    history = (
        await s.execute(
            select(AuditEvent, actor.c.name)
            .outerjoin(actor, actor.c.id == AuditEvent.actor_id)
            .where(
                AuditEvent.target == row.id,
                or_(
                    AuditEvent.action.like("explore.%"),
                    AuditEvent.action.in_(
                        ("lesson.review", "lesson.moderate", "lesson.visibility", "lesson.explore_opt_out")
                    ),
                ),
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(30)
        )
    ).all()
    return {
        **_item(row, owner_name, owner_status, cur, spread.get(row.id), mean, ps),
        "rule": _rule_out(ps),
        "ratings_total": int(total or 0),
        "ratings": [
            {
                "name": name,
                "stars": r.stars,
                "comment": r.comment,
                "at": r.updated_at,
                "counts": status == "active" and r.user_id != row.owner_id,
                "why_not": (
                    "the lesson's owner"
                    if r.user_id == row.owner_id
                    else ("a suspended account" if status != "active" else None)
                ),
            }
            for r, name, status in ratings
        ],
        "history": [
            {"action": e.action, "by": by, "detail": e.detail, "at": e.created_at} for e, by in history
        ],
    }


@router.put("/explore/{lesson_id}")
async def curate(
    lesson_id: str, body: ExploreIn, request: Request, p: Curator, platform: Plat, s: DB
) -> dict[str, Any]:
    ps = platform.settings
    row = await s.get(Lesson, lesson_id)
    if row is None:
        raise HTTPException(404, "No such lesson.")
    if row.deleted_at is not None:
        raise HTTPException(409, "This lesson was removed. Restore it first.")
    if body.subject is not None and body.subject not in SUBJECTS:
        raise HTTPException(422, "Unknown subject.")
    shared = False
    if body.action == "include":
        if row.status != "complete":
            raise HTTPException(409, "A lesson can be added to Explore once it is ready.")
        if row.visibility == "private":
            if not body.share:
                raise HTTPException(
                    409,
                    "This lesson is private. Adding it to Explore shares it with a link: confirm to go on.",
                )
            row.visibility = "unlisted"
            shared = True
    cur = await s.get(Curation, row.id)
    if cur is None:
        cur = Curation(lesson_id=row.id)
        s.add(cur)
    cur.pin = {"include": "include", "exclude": "exclude"}.get(body.action, "")
    cur.pin_note = body.note
    cur.pinned_by = p.user_id
    cur.pinned_at = time.time()
    moved_from = None
    if body.subject is not None and body.subject != row.subject:
        moved_from = row.subject
        row.subject = body.subject
    was_listed = row.explore_listed
    await s.flush()
    listed = await curation.refresh(s, ps, [row.id])
    await s.commit()
    platform.forget_lesson(row.id)
    detail: dict[str, Any] = {"note": body.note}
    if shared:
        detail["shared"] = True
    if moved_from is not None:
        detail["subject"] = [moved_from, row.subject]
    await platform.audit(f"explore.{body.action}", p.user_id, row.id, client_ip(request), **detail)
    owner = row.owner_id if row.owner_id and row.owner_id != p.user_id else None
    title = row.title or row.topic
    if owner and shared and row.explore_listed:
        listed = [e for e in listed if e.lesson_id != row.id]
        await platform.notify(
            owner,
            "explore",
            f"Your lesson is in Explore: {title}",
            "An administrator shared it with a link and added it to Explore, under its subject.",
            f"/lessons/{row.id}",
        )
    elif owner and was_listed and not row.explore_listed:
        await platform.notify(
            owner,
            "explore",
            f"Your lesson left Explore: {title}",
            "An administrator took it out of Explore. Anyone with its link can still watch it.",
            f"/lessons/{row.id}",
        )
    await curation.announce(platform, listed)
    row, owner_name, owner_status, cur = await _one(s, row.id)
    spread = await curation.distribution(s, [row.id])
    return _item(row, owner_name, owner_status, cur, spread.get(row.id), await curation.platform_mean(s), ps)


@router.put("/ratings/lessons/{lesson_id}/review")
async def review(
    lesson_id: str, body: ReviewIn, request: Request, p: Reviewer, platform: Plat, s: DB
) -> dict[str, Any]:
    row = await s.get(Lesson, lesson_id)
    if row is None:
        raise HTTPException(404, "No such lesson.")
    cur = await s.get(Curation, row.id)
    if cur is None:
        cur = Curation(lesson_id=row.id)
        s.add(cur)
    before = cur.review
    cur.review = body.status
    cur.review_note = body.note
    cur.reviewed_by = p.user_id
    cur.reviewed_at = time.time()
    await s.commit()
    await platform.audit(
        "lesson.review", p.user_id, row.id, client_ip(request), status=[before, body.status], note=body.note
    )
    return {"status": cur.review, "note": cur.review_note, "at": cur.reviewed_at}
