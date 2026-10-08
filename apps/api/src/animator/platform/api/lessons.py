"""/api/v1/lessons and /api/v1/explore: a person's lessons, the public catalog, and what a learner keeps
about a lesson (progress, saved, notes, rating, doubts).

Making a lesson goes to the pipeline's own runner, unchanged (`runner.start`); the platform checks who
may ask for it first (permission, daily quota, the longest lesson for the role) and records the owner.
List queries fetch each lesson with its owner, the learner's progress and saved flag in one statement.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from animator.pipeline.models import GenerationRequest
from animator.platform import curation, usage
from animator.platform.catalog import SUBJECTS, new_row
from animator.platform.core import Principal
from animator.platform.deps import DB, MaybeMe, Me, Plat, client_ip
from animator.platform.models import (
    ActivityDay,
    Curation,
    DoubtLog,
    Lesson,
    Note,
    Progress,
    Rating,
    Saved,
    User,
)
from animator.platform.rbac import Perm
from animator.platform.schemas import (
    LessonCreateIn,
    LessonPatchIn,
    NoteIn,
    NotePatchIn,
    ProgressIn,
    RatingIn,
)

router = APIRouter(prefix="/api/v1", tags=["lessons"])

DONE_AT = 0.95  # watched this share of a lesson: complete
# Explore: the stored decision (curation.decide), with its plain conditions again as a safety net.
IN_EXPLORE = (Lesson.explore_listed.is_(True), Lesson.deleted_at.is_(None), Lesson.status == "complete")


def lesson_out(
    row: Lesson,
    me: Principal | None,
    owner_name: str | None = None,
    progress: Progress | None = None,
    saved: bool = False,
) -> dict[str, Any]:
    mine = me is not None and row.owner_id == me.user_id
    staff = me is not None and me.can(Perm.LESSON_ANY)
    curator = me is not None and me.can(Perm.EXPLORE_CURATE)
    return {
        "id": row.id,
        "title": row.title or row.topic,
        "subtitle": row.subtitle,
        "topic": row.topic,
        "audience": row.audience,
        "language": row.language,
        "duration_min": row.duration_min,
        "length_s": row.planned_s,
        "scene_count": row.scene_count,
        "status": row.status,
        "subject": row.subject,
        "subject_label": SUBJECTS.get(row.subject, "General"),
        "visibility": row.visibility,
        "featured": row.featured,
        "views": row.views,
        "rating": round(row.rating_sum / row.rating_count, 2) if row.rating_count else None,
        "rating_count": row.rating_count,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "completed_at": row.completed_at,
        "deleted": row.deleted_at is not None,
        "owner": {"id": row.owner_id, "name": owner_name or "Animator"} if row.owner_id else None,
        "mine": mine,
        "can_edit": mine or staff,
        "can_rate": me is not None and not mine and row.status == "complete",
        "in_explore": row.explore_listed,
        # Why it is in Explore (owner: published; auto: its ratings; admin: added by an admin) and the
        # owner's choice not to be listed by ratings: for the owner and the people who curate.
        "explore": (
            {"source": row.explore_source, "since": row.explore_at, "opt_out": row.explore_opt_out}
            if (mine or staff or curator)
            else None
        ),
        "cost_usd": row.cost_usd if (mine or staff) else None,
        "progress": (
            {
                "position_s": progress.position_s,
                "percent": round(progress.percent, 3),
                "completed": progress.completed_at is not None,
                "last_watched_at": progress.last_watched_at,
            }
            if progress
            else None
        ),
        "saved": saved,
    }


def _joined(me: Principal | None) -> Any:
    """Lessons with owner name, and (for a signed-in learner) their progress and saved flag."""
    uid = me.user_id if me else ""
    return (
        select(Lesson, User.name, Progress, Saved.created_at)
        .outerjoin(User, User.id == Lesson.owner_id)
        .outerjoin(Progress, and_(Progress.lesson_id == Lesson.id, Progress.user_id == uid))
        .outerjoin(Saved, and_(Saved.lesson_id == Lesson.id, Saved.user_id == uid))
    )


def _rows(result: Any, me: Principal | None) -> list[dict[str, Any]]:
    return [lesson_out(r[0], me, r[1], r[2], r[3] is not None) for r in result.all()]


def _search(q: str | None) -> Any:
    if not q or not q.strip():
        return None
    term = f"%{q.strip()[:80].lower()}%"
    return or_(func.lower(Lesson.title).like(term), func.lower(Lesson.topic).like(term))


async def readable(s: AsyncSession, lesson_id: str, me: Principal | None) -> Lesson:
    row = await s.get(Lesson, lesson_id)
    if row is None:
        raise HTTPException(404, "No such lesson.")
    staff = me is not None and me.can(Perm.LESSON_ANY)
    mine = me is not None and row.owner_id == me.user_id and row.deleted_at is None
    shared = row.deleted_at is None and row.visibility in ("public", "unlisted")
    if not (staff or mine or shared):
        raise HTTPException(404, "No such lesson.")
    return row


async def editable(s: AsyncSession, lesson_id: str, me: Principal) -> Lesson:
    row = await readable(s, lesson_id, me)
    if not (me.can(Perm.LESSON_ANY) or row.owner_id == me.user_id):
        raise HTTPException(403, "Only the lesson's owner can change it.")
    return row


# ---------------------------------------------------------------------- create and list


@router.post("/lessons", status_code=201)
async def create_lesson(
    body: LessonCreateIn, request: Request, p: Me, platform: Plat, s: DB
) -> dict[str, Any]:
    ps = platform.settings
    if not p.can(Perm.LESSON_CREATE):
        raise HTTPException(403, "Your account cannot create lessons.")
    if ps.require_verified_email and not p.verified:
        raise HTTPException(403, "Confirm your email first: we sent you a link.")
    if body.visibility == "public" and not p.can(Perm.LESSON_PUBLISH):
        raise HTTPException(
            403, "Only educators can publish lessons to Explore. Share it with a link instead."
        )
    longest = ps.max_minutes(p.role)
    if body.duration_min > longest:
        raise HTTPException(422, f"Lessons can be at most {longest:g} minutes long on your plan.")
    refused = await usage.check(s, ps, p.user_id)
    if refused is not None:
        raise HTTPException(402, refused)
    wait = platform.limiter.hit(f"create:{p.user_id}", 6, 600)
    if wait is not None:
        raise HTTPException(429, "You started several lessons just now. Wait a few minutes.")
    quota = ps.lessons_per_day(p.role)
    if quota > 0:
        made = await s.scalar(
            select(func.count())
            .select_from(Lesson)
            .where(Lesson.owner_id == p.user_id, Lesson.created_at > time.time() - 86400)
        )
        if (made or 0) >= quota:
            raise HTTPException(
                429, f"You have created {quota} lessons in the last 24 hours, your daily limit."
            )
    runner = platform.runner
    if runner is None:
        raise HTTPException(503, "The lesson service is starting. Try again in a moment.")
    request_ = GenerationRequest(
        topic=body.topic,
        audience=body.audience,
        duration_min=body.duration_min,
        style=body.style or None,
        language=body.language,
        paced=body.paced,
        layout=body.layout,
    )
    gen = await runner.start(request_)
    row = new_row(gen, p.user_id)
    row.visibility = body.visibility
    if body.subject in SUBJECTS:
        row.subject = body.subject
    s.add(row)
    await s.commit()
    platform.forget_lesson(gen.id)
    if getattr(platform, "catalog", None) is not None:
        platform.catalog.follow(gen.id)
    await platform.audit("lesson.create", p.user_id, gen.id, client_ip(request), minutes=body.duration_min)
    return lesson_out(row, p, p.name)


@router.get("/lessons")
async def my_lessons(
    p: Me,
    s: DB,
    q: str | None = None,
    status: str = "all",
    sort: str = "new",
    offset: int = 0,
    limit: int = 24,
) -> dict[str, Any]:
    limit = max(1, min(limit, 60))
    offset = max(0, offset)
    stmt = _joined(p).where(Lesson.owner_id == p.user_id, Lesson.deleted_at.is_(None))
    if status == "ready":
        stmt = stmt.where(Lesson.status == "complete")
    elif status == "generating":
        stmt = stmt.where(Lesson.status.in_(("queued", "planning", "generating")))
    elif status == "stopped":
        stmt = stmt.where(Lesson.status.in_(("failed", "cancelled", "paused")))
    elif status == "shared":
        stmt = stmt.where(Lesson.visibility != "private")
    if (cond := _search(q)) is not None:
        stmt = stmt.where(cond)
    order = {
        "old": [Lesson.created_at.asc()],
        "title": [func.lower(Lesson.title).asc()],
        "recent": [func.coalesce(Progress.last_watched_at, Lesson.created_at).desc()],
    }.get(sort, [Lesson.created_at.desc()])
    result = await s.execute(stmt.order_by(*order).offset(offset).limit(limit + 1))
    items = _rows(result, p)
    return {"items": items[:limit], "has_more": len(items) > limit}


@router.get("/explore")
async def explore(
    me: MaybeMe,
    s: DB,
    q: str | None = None,
    subject: str | None = None,
    sort: str = "popular",
    offset: int = 0,
    limit: int = 24,
) -> dict[str, Any]:
    limit = max(1, min(limit, 60))
    stmt = _joined(me).where(*IN_EXPLORE)
    if subject and subject in SUBJECTS:
        stmt = stmt.where(Lesson.subject == subject)
    if (cond := _search(q)) is not None:
        stmt = stmt.where(cond)
    rating = func.coalesce(Lesson.rating_sum * 1.0 / func.nullif(Lesson.rating_count, 0), 0)
    orders: dict[str, list[Any]] = {
        "new": [Lesson.created_at.desc()],
        "top": [rating.desc(), Lesson.rating_count.desc()],
    }
    order = orders.get(sort, [Lesson.featured.desc(), Lesson.views.desc(), Lesson.created_at.desc()])
    result = await s.execute(stmt.order_by(*order).offset(max(0, offset)).limit(limit + 1))
    items = _rows(result, me)
    return {"items": items[:limit], "has_more": len(items) > limit}


@router.get("/explore/subjects")
async def subjects(s: DB) -> list[dict[str, Any]]:
    counts = dict(
        (
            await s.execute(select(Lesson.subject, func.count()).where(*IN_EXPLORE).group_by(Lesson.subject))
        ).all()
    )
    return [{"key": k, "label": v, "count": int(counts.get(k, 0))} for k, v in SUBJECTS.items()]


@router.get("/explore/featured")
async def featured(me: MaybeMe, s: DB) -> list[dict[str, Any]]:
    stmt = (
        _joined(me)
        .where(*IN_EXPLORE)
        .order_by(Lesson.featured.desc(), Lesson.views.desc(), Lesson.created_at.desc())
        .limit(8)
    )
    return _rows(await s.execute(stmt), me)


# ---------------------------------------------------------------------- one lesson


@router.get("/lessons/{lesson_id}")
async def get_lesson(lesson_id: str, me: MaybeMe, platform: Plat, s: DB) -> dict[str, Any]:
    row = await readable(s, lesson_id, me)
    result = await s.execute(_joined(me).where(Lesson.id == row.id))
    out = _rows(result, me)[0]
    if me is not None:
        mine = await s.get(Rating, (me.user_id, row.id))
        out["my_rating"] = {"stars": mine.stars, "comment": mine.comment} if mine else None
    if out["explore"] is not None:
        ps = platform.settings
        pin = await s.scalar(select(Curation.pin).where(Curation.lesson_id == row.id))
        out["explore"].update(
            included=pin == "include",
            excluded=pin == "exclude",
            rule={
                "auto": ps.explore_auto,
                "min_raters": ps.explore_min_raters,
                "min_average": ps.explore_min_average,
            },
        )
    return out


@router.patch("/lessons/{lesson_id}")
async def patch_lesson(
    lesson_id: str, body: LessonPatchIn, request: Request, p: Me, platform: Plat, s: DB
) -> dict[str, Any]:
    row = await editable(s, lesson_id, p)
    # Every check first, then the changes: a refusal leaves nothing half done.
    visibility = body.visibility if body.visibility not in (None, row.visibility) else None
    opt_out = body.explore_opt_out if body.explore_opt_out not in (None, row.explore_opt_out) else None
    if visibility == "public" and not p.can(Perm.LESSON_PUBLISH):
        raise HTTPException(403, "Only educators can publish to Explore. Share it with a link instead.")
    if visibility == "public" and row.status != "complete":
        raise HTTPException(409, "A lesson can be published once it is ready.")
    if opt_out is not None and row.owner_id != p.user_id:
        raise HTTPException(403, "Only the lesson's owner can choose this.")
    if body.subject is not None and body.subject not in SUBJECTS:
        raise HTTPException(422, "Unknown subject.")
    if body.featured is not None and not p.can(Perm.LESSON_FEATURE):
        raise HTTPException(403, "Only admins can feature lessons.")
    if visibility == "private":
        # Private is private: an admin's "add to Explore" ends with it (sharing it again later does not
        # bring it back by surprise).
        cur = await s.get(Curation, row.id)
        if cur is not None and cur.pin == "include":
            cur.pin, cur.pin_note, cur.pinned_by, cur.pinned_at = (
                "",
                "Made private by its owner",
                p.user_id,
                time.time(),
            )
    if visibility is not None:
        row.visibility = visibility
    if opt_out is not None:
        row.explore_opt_out = opt_out
    if body.subject is not None:
        row.subject = body.subject
    if body.featured is not None:
        row.featured = body.featured
    relist = visibility is not None or opt_out is not None
    listed = await curation.refresh(s, platform.settings, [row.id]) if relist else []
    await s.commit()
    platform.forget_lesson(row.id)
    # Records after the commit: they are written on their own connection, which would otherwise wait
    # for this request's open transaction (SQLite has one writer).
    ip = client_ip(request)
    if visibility is not None:
        await platform.audit("lesson.visibility", p.user_id, row.id, ip, to=visibility)
    if opt_out is not None:
        await platform.audit("lesson.explore_opt_out", p.user_id, row.id, ip, opt_out=opt_out)
    await curation.announce(platform, listed)
    return await get_lesson(row.id, p, platform, s)


@router.delete("/lessons/{lesson_id}", status_code=204)
async def delete_lesson(lesson_id: str, request: Request, p: Me, platform: Plat, s: DB) -> Response:
    row = await editable(s, lesson_id, p)
    row.deleted_at = time.time()
    row.visibility = "private"
    row.featured = False
    await curation.refresh(s, platform.settings, [row.id])  # out of Explore
    await s.commit()
    platform.forget_lesson(row.id)
    runner = platform.runner
    if runner is not None and runner.running(row.id):
        await runner.cancel(row.id)  # stop spending on a lesson nobody will watch
    await platform.audit("lesson.delete", p.user_id, row.id, client_ip(request))
    return Response(status_code=204)


# ---------------------------------------------------------------------- learning on a lesson


def _today(day: str | None) -> str:
    """The learner's day if it is plausible (within a day of UTC), else the UTC day."""
    utc = dt.datetime.now(dt.UTC).date()
    if day:
        try:
            d = dt.date.fromisoformat(day)
            if abs((d - utc).days) <= 1:
                return d.isoformat()
        except ValueError:
            pass
    return utc.isoformat()


@router.put("/lessons/{lesson_id}/progress")
async def save_progress(lesson_id: str, body: ProgressIn, p: Me, platform: Plat, s: DB) -> dict[str, Any]:
    row = await readable(s, lesson_id, p)
    wait = platform.limiter.hit(f"progress:{p.user_id}", 240, 3600)
    if wait is not None:
        raise HTTPException(429, "Too many updates.")
    now = time.time()
    prog = await s.get(Progress, (p.user_id, row.id))
    duration = max(body.duration_s, row.planned_s, 1.0)
    first = prog is None
    if prog is None:
        prog = Progress(user_id=p.user_id, lesson_id=row.id, started_at=now)
        s.add(prog)
        row.views = (row.views or 0) + 1
    # Watched time can only grow as fast as the clock (a report every few seconds, at most 2x speed).
    elapsed = now - (prog.last_watched_at if not first else now - 30)
    delta = min(body.delta_s, max(0.0, elapsed * 2.2), 600.0)
    prog.position_s = min(body.position_s, duration)
    prog.duration_s = duration
    prog.percent = max(prog.percent or 0.0, min(1.0, body.position_s / duration))
    prog.watched_s = (prog.watched_s or 0.0) + delta
    prog.last_watched_at = now
    newly_done = False
    if prog.completed_at is None and body.position_s / duration >= DONE_AT and row.status == "complete":
        prog.completed_at = now
        newly_done = True
    if delta > 0:
        day = _today(body.day)
        act = await s.get(ActivityDay, (p.user_id, day))
        if act is None:
            s.add(ActivityDay(user_id=p.user_id, day=day, seconds=delta))
        else:
            act.seconds += delta
    await s.commit()
    return {
        "position_s": prog.position_s,
        "percent": round(prog.percent, 3),
        "completed": prog.completed_at is not None,
        "newly_completed": newly_done,
    }


@router.put("/lessons/{lesson_id}/save", status_code=204)
async def save_lesson(lesson_id: str, p: Me, s: DB) -> Response:
    row = await readable(s, lesson_id, p)
    if await s.get(Saved, (p.user_id, row.id)) is None:
        s.add(Saved(user_id=p.user_id, lesson_id=row.id))
        await s.commit()
    return Response(status_code=204)


@router.delete("/lessons/{lesson_id}/save", status_code=204)
async def unsave_lesson(lesson_id: str, p: Me, s: DB) -> Response:
    hit = await s.get(Saved, (p.user_id, lesson_id))
    if hit is not None:
        await s.delete(hit)
        await s.commit()
    return Response(status_code=204)


def note_out(n: Note) -> dict[str, Any]:
    return {"id": n.id, "lesson_id": n.lesson_id, "t_s": n.t_s, "text": n.text, "created_at": n.created_at}


@router.get("/lessons/{lesson_id}/notes")
async def notes(lesson_id: str, p: Me, s: DB) -> list[dict[str, Any]]:
    await readable(s, lesson_id, p)
    rows = await s.scalars(
        select(Note)
        .where(Note.user_id == p.user_id, Note.lesson_id == lesson_id)
        .order_by(Note.t_s)
        .limit(500)
    )
    return [note_out(n) for n in rows.all()]


@router.post("/lessons/{lesson_id}/notes", status_code=201)
async def add_note(lesson_id: str, body: NoteIn, p: Me, platform: Plat, s: DB) -> dict[str, Any]:
    await readable(s, lesson_id, p)
    if platform.limiter.hit(f"notes:{p.user_id}", 120, 3600) is not None:
        raise HTTPException(429, "Too many notes at once.")
    count = await s.scalar(
        select(func.count()).select_from(Note).where(Note.user_id == p.user_id, Note.lesson_id == lesson_id)
    )
    if (count or 0) >= 500:
        raise HTTPException(409, "This lesson already has 500 notes.")
    n = Note(user_id=p.user_id, lesson_id=lesson_id, t_s=body.t_s, text=body.text)
    s.add(n)
    await s.commit()
    return note_out(n)


@router.patch("/notes/{note_id}")
async def edit_note(note_id: str, body: NotePatchIn, p: Me, s: DB) -> dict[str, Any]:
    n = await s.get(Note, note_id)
    if n is None or n.user_id != p.user_id:
        raise HTTPException(404, "No such note.")
    n.text = body.text
    await s.commit()
    return note_out(n)


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(note_id: str, p: Me, s: DB) -> Response:
    n = await s.get(Note, note_id)
    if n is None or n.user_id != p.user_id:
        raise HTTPException(404, "No such note.")
    await s.delete(n)
    await s.commit()
    return Response(status_code=204)


@router.put("/lessons/{lesson_id}/rating")
async def rate(lesson_id: str, body: RatingIn, p: Me, platform: Plat, s: DB) -> dict[str, Any]:
    row = await readable(s, lesson_id, p)
    if row.status != "complete":
        raise HTTPException(409, "Rate a lesson once it is ready.")
    if row.owner_id == p.user_id:
        raise HTTPException(403, "You cannot rate your own lesson.")
    if platform.limiter.hit(f"rating:{p.user_id}", 60, 3600) is not None:
        raise HTTPException(429, "You changed many ratings just now. Try again in a while.")
    # One rating at a time per lesson: the row lock serializes them (PostgreSQL; SQLite has one writer).
    await s.execute(select(Lesson.id).where(Lesson.id == row.id).with_for_update())
    mine = await s.get(Rating, (p.user_id, row.id))
    if mine is None:
        mine = Rating(user_id=p.user_id, lesson_id=row.id, stars=body.stars, comment=body.comment or "")
        s.add(mine)
    else:
        mine.stars = body.stars
        if body.comment is not None:
            mine.comment = body.comment
    await s.flush()
    await curation.recount(s, [row.id])  # from the ratings themselves: the numbers cannot drift
    listed = await curation.refresh(s, platform.settings, [row.id])
    await s.commit()
    await curation.announce(platform, listed)
    return {
        "rating": round(row.rating_sum / row.rating_count, 2) if row.rating_count else None,
        "rating_count": row.rating_count,
        "my_rating": {"stars": mine.stars, "comment": mine.comment},
    }


@router.get("/lessons/{lesson_id}/doubts")
async def lesson_doubts(lesson_id: str, p: Me, s: DB) -> list[dict[str, Any]]:
    await readable(s, lesson_id, p)
    rows = await s.scalars(
        select(DoubtLog)
        .where(DoubtLog.user_id == p.user_id, DoubtLog.lesson_id == lesson_id)
        .order_by(DoubtLog.created_at.desc())
        .limit(100)
    )
    return [
        {
            "session_id": d.session_id,
            "turn": d.turn,
            "question": d.question,
            "title": d.title,
            "kind": d.kind,
            "created_at": d.created_at,
        }
        for d in rows.all()
    ]
