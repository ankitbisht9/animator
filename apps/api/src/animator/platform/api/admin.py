"""/api/v1/admin: the platform at a glance, people (roles, suspension), every lesson (sharing,
featuring, removal, one at a time or many at once), the audit log, and the system (engine workers,
request metrics). Sharing follows `sharing.apply` (ADR-057): featured lessons are public ones."""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import and_, case, func, or_, select

from animator.platform import curation, sharing
from animator.platform.accounts import revoke_sessions
from animator.platform.api.lessons import _joined, _rows, _search
from animator.platform.catalog import SUBJECTS
from animator.platform.deps import DB, Admin, Analyst, Auditor, Moderator, Plat, client_ip
from animator.platform.models import (
    ActivityDay,
    AuditEvent,
    DoubtLog,
    Lesson,
    Progress,
    Rating,
    Ticket,
    UsageEntry,
    User,
)
from animator.platform.schemas import AdminLessonPatchIn, AdminLessonsBulkIn, UserPatchIn

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _days(n: int) -> list[str]:
    today = dt.datetime.now(dt.UTC).date()
    return [(today - dt.timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def _day_of(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.UTC).date().isoformat()


@router.get("/overview")
async def overview(p: Admin, s: DB) -> dict[str, Any]:
    now = time.time()
    week = now - 7 * 86400

    async def one(stmt: Any) -> float:
        return float(await s.scalar(stmt) or 0)

    users = await one(select(func.count()).select_from(User))
    new_users = await one(select(func.count()).select_from(User).where(User.created_at > week))
    week_days = _days(7)
    active = await one(
        select(func.count(func.distinct(ActivityDay.user_id))).where(ActivityDay.day >= week_days[0])
    )
    lessons = await one(select(func.count()).select_from(Lesson).where(Lesson.deleted_at.is_(None)))
    new_lessons = await one(select(func.count()).select_from(Lesson).where(Lesson.created_at > week))
    # Spend from the usage ledger (ADR-053): AI and voice, making lessons and learning.
    cost = await one(select(func.coalesce(func.sum(UsageEntry.cost_micros), 0))) / 1_000_000
    cost_week = (
        await one(
            select(func.coalesce(func.sum(UsageEntry.cost_micros), 0)).where(UsageEntry.created_at > week)
        )
        / 1_000_000
    )
    doubts = await one(select(func.count()).select_from(DoubtLog))
    tickets = await one(
        select(func.count()).select_from(Ticket).where(Ticket.status.in_(("open", "pending")))
    )
    completions = await one(
        select(func.count()).select_from(Progress).where(Progress.completed_at.is_not(None))
    )
    learned = await one(select(func.coalesce(func.sum(ActivityDay.seconds), 0)))
    by_status = dict(
        (
            await s.execute(
                select(Lesson.status, func.count()).where(Lesson.deleted_at.is_(None)).group_by(Lesson.status)
            )
        ).all()
    )
    by_role = dict((await s.execute(select(User.role, func.count()).group_by(User.role))).all())
    by_subject = dict(
        (
            await s.execute(
                select(Lesson.subject, func.count())
                .where(Lesson.deleted_at.is_(None))
                .group_by(Lesson.subject)
            )
        ).all()
    )
    days = _days(14)
    since = now - 14 * 86400
    signups = [
        _day_of(t) for t in (await s.scalars(select(User.created_at).where(User.created_at > since))).all()
    ]
    made = [
        _day_of(t)
        for t in (await s.scalars(select(Lesson.created_at).where(Lesson.created_at > since))).all()
    ]
    minutes = dict(
        (
            await s.execute(
                select(ActivityDay.day, func.sum(ActivityDay.seconds))
                .where(ActivityDay.day >= days[0])
                .group_by(ActivityDay.day)
            )
        ).all()
    )
    return {
        "users": int(users),
        "new_users_7d": int(new_users),
        "active_users_7d": int(active),
        "lessons": int(lessons),
        "new_lessons_7d": int(new_lessons),
        "ai_cost_usd": round(cost, 4),
        "ai_cost_7d_usd": round(cost_week, 4),
        "doubts": int(doubts),
        "open_tickets": int(tickets),
        "completions": int(completions),
        "hours_learned": round(learned / 3600, 1),
        "lessons_by_status": {k: int(v) for k, v in by_status.items()},
        "users_by_role": {k: int(v) for k, v in by_role.items()},
        "lessons_by_subject": {k: int(v) for k, v in by_subject.items()},
        "daily": [
            {
                "day": d,
                "signups": signups.count(d),
                "lessons": made.count(d),
                "minutes": round(float(minutes.get(d, 0) or 0) / 60, 1),
            }
            for d in days
        ],
    }


@router.get("/users")
async def users(
    p: Admin,
    s: DB,
    q: str | None = None,
    role: str | None = None,
    status: str | None = None,
    offset: int = 0,
    limit: int = 25,
) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    stmt = select(User)
    if q and q.strip():
        term = f"%{q.strip()[:80].lower()}%"
        stmt = stmt.where(func.lower(User.email).like(term) | func.lower(User.name).like(term))
    if role in ("learner", "educator", "admin"):
        stmt = stmt.where(User.role == role)
    if status in ("active", "suspended"):
        stmt = stmt.where(User.status == status)
    total = await s.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await s.scalars(stmt.order_by(User.created_at.desc()).offset(max(0, offset)).limit(limit))).all()
    ids = [u.id for u in rows]
    made = (
        dict(
            (
                await s.execute(
                    select(Lesson.owner_id, func.count())
                    .where(Lesson.owner_id.in_(ids))
                    .group_by(Lesson.owner_id)
                )
            ).all()
        )
        if ids
        else {}
    )
    return {
        "total": int(total or 0),
        "items": [
            {
                "id": u.id,
                "email": u.email,
                "name": u.name,
                "role": u.role,
                "status": u.status,
                "verified": u.email_verified_at is not None,
                "avatar": u.avatar,
                "created_at": u.created_at,
                "last_login_at": u.last_login_at,
                "lessons": int(made.get(u.id, 0)),
                "me": u.id == p.user_id,
            }
            for u in rows
        ],
    }


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: str, body: UserPatchIn, request: Request, p: Admin, platform: Plat, s: DB
) -> dict[str, Any]:
    user = await s.get(User, user_id)
    if user is None:
        raise HTTPException(404, "No such person.")
    if user.id == p.user_id and (body.role not in (None, "admin") or body.status == "suspended"):
        raise HTTPException(409, "You cannot remove your own admin role or suspend yourself.")
    changes: dict[str, Any] = {}
    if body.role is not None and body.role != user.role:
        changes["role"] = [user.role, body.role]
        user.role = body.role
    listed: list[curation.Listed] = []
    if body.status is not None and body.status != user.status:
        changes["status"] = [user.status, body.status]
        user.status = body.status
        if body.status == "suspended":
            await revoke_sessions(s, platform, user.id, "suspended")
        # A suspended account's ratings stop counting and its lessons leave Explore (unless an admin
        # added them); both come back when it is active again.
        await s.flush()
        rated = (await s.scalars(select(Rating.lesson_id).where(Rating.user_id == user.id))).all()
        owned = (await s.scalars(select(Lesson.id).where(Lesson.owner_id == user.id))).all()
        await curation.recount(s, rated)
        listed = await curation.refresh(s, platform.settings, [*rated, *owned])
    await s.commit()
    platform.forget(user_id=user.id)
    await curation.announce(platform, listed)
    if changes:
        await platform.audit("user.update", p.user_id, user.id, client_ip(request), **changes)
        if "role" in changes:
            article = "an" if body.role in ("admin", "educator") else "a"
            await platform.notify(user.id, "role", f"You are now {article} {body.role}", "", "/profile")
        if body.role == "admin" and getattr(platform, "catalog", None) is not None:
            await platform.catalog.claim_orphans(user.id)
    return {"id": user.id, "role": user.role, "status": user.status}


@router.post("/users/{user_id}/revoke-sessions")
async def end_sessions(user_id: str, request: Request, p: Admin, platform: Plat, s: DB) -> dict[str, Any]:
    n = await revoke_sessions(s, platform, user_id, "signed out by admin")
    await s.commit()
    await platform.audit("user.sessions_revoked", p.user_id, user_id, client_ip(request), count=n)
    return {"signed_out": n}


# The admin list's orders (`sort`), newest first by default.
_ORDERS: dict[str, list[Any]] = {
    "new": [Lesson.created_at.desc()],
    "old": [Lesson.created_at.asc()],
    "views": [Lesson.views.desc(), Lesson.created_at.desc()],
    "cost": [Lesson.cost_usd.desc(), Lesson.created_at.desc()],
    "title": [func.lower(Lesson.title).asc(), Lesson.created_at.desc()],
}
_STATUSES = ("queued", "planning", "generating", "complete", "failed", "cancelled", "paused")


def _admin_filters(q: str | None, status: str | None, subject: str | None) -> list[Any]:
    """What narrows the admin list besides its tab: words (title, topic, owner's name or the lesson's
    id), statuses (comma separated) and a subject."""
    conds: list[Any] = []
    if q and q.strip():
        term = f"%{q.strip()[:80].lower()}%"
        conds.append(or_(_search(q), func.lower(User.name).like(term), func.lower(Lesson.id).like(term)))
    wanted = [x for x in (status or "").split(",") if x in _STATUSES]
    if wanted:
        conds.append(Lesson.status.in_(wanted))
    if subject and subject in SUBJECTS:
        conds.append(Lesson.subject == subject)
    return conds


@router.get("/lessons")
async def admin_lessons(
    p: Moderator,
    s: DB,
    q: str | None = None,
    status: str | None = None,
    visibility: str | None = None,
    featured: bool | None = None,
    subject: str | None = None,
    deleted: bool = False,
    sort: str = "new",
    offset: int = 0,
    limit: int = 25,
) -> dict[str, Any]:
    """Every lesson, a page at a time, with how many each tab holds (for the words and filters given)."""
    limit = max(1, min(limit, 100))
    conds = _admin_filters(q, status, subject)
    stmt = _joined(p).where(
        Lesson.deleted_at.is_not(None) if deleted else Lesson.deleted_at.is_(None), *conds
    )
    if visibility in sharing.VISIBILITIES:
        stmt = stmt.where(Lesson.visibility == visibility)
    if featured is not None:
        stmt = stmt.where(Lesson.featured.is_(featured))
    total = await s.scalar(select(func.count()).select_from(stmt.with_only_columns(Lesson.id).subquery()))
    order = _ORDERS.get(sort, _ORDERS["new"])
    result = await s.execute(stmt.order_by(*order, Lesson.id).offset(max(0, offset)).limit(limit))
    live = Lesson.deleted_at.is_(None)

    def n(*when: Any) -> Any:
        return func.coalesce(func.sum(case((and_(*when), 1), else_=0)), 0)

    counted = (
        await s.execute(
            select(
                n(live),
                n(live, Lesson.visibility == "public"),
                n(live, Lesson.visibility == "public", Lesson.featured.is_(True)),
                n(live, Lesson.visibility == "unlisted"),
                n(live, Lesson.visibility == "private"),
                n(Lesson.deleted_at.is_not(None)),
            )
            .select_from(Lesson)
            .outerjoin(User, User.id == Lesson.owner_id)
            .where(*conds)
        )
    ).one()
    keys = ("all", "public", "featured", "unlisted", "private", "deleted")
    return {
        "total": int(total or 0),
        "items": _rows(result, p),
        "counts": {k: int(v or 0) for k, v in zip(keys, counted, strict=True)},
    }


@router.patch("/lessons/{lesson_id}")
async def moderate(
    lesson_id: str, body: AdminLessonPatchIn, request: Request, p: Moderator, platform: Plat, s: DB
) -> dict[str, Any]:
    row = await s.get(Lesson, lesson_id)
    if row is None:
        raise HTTPException(404, "No such lesson.")
    if body.subject is not None and body.subject not in SUBJECTS:
        raise HTTPException(422, "Unknown subject.")
    try:
        sharing.apply(row, visibility=body.visibility, featured=body.featured, deleted=body.deleted)
    except sharing.SharingError as e:
        raise HTTPException(409, str(e)) from e
    if body.subject is not None:
        row.subject = body.subject
    listed = await curation.refresh(s, platform.settings, [row.id])
    await s.commit()
    platform.forget_lesson(row.id)
    await curation.announce(platform, listed)
    await platform.audit(
        "lesson.moderate", p.user_id, row.id, client_ip(request), **body.model_dump(exclude_none=True)
    )
    result = await s.execute(_joined(p).where(Lesson.id == row.id))
    return _rows(result, p)[0]


# What each bulk action asks of every lesson it is given (ADR-057).
_BULK: dict[str, dict[str, Any]] = {
    "publish": {"visibility": "public"},
    "unlist": {"visibility": "unlisted"},
    "private": {"visibility": "private"},
    "feature": {"featured": True},
    "unfeature": {"featured": False},
    "delete": {"deleted": True},
    "restore": {"deleted": False},
}


@router.post("/lessons/bulk")
async def bulk_lessons(
    body: AdminLessonsBulkIn, request: Request, p: Moderator, platform: Plat, s: DB
) -> dict[str, Any]:
    """One action on many lessons. Each lesson follows the same rules as one at a time: those the rules
    refuse are skipped with the reason, the others change together."""
    rows = {r.id: r for r in (await s.scalars(select(Lesson).where(Lesson.id.in_(body.ids)))).all()}
    now = time.time()
    changed: list[str] = []
    unchanged: list[str] = []
    skipped: list[dict[str, str]] = []
    for lid in body.ids:
        row = rows.get(lid)
        if row is None:
            skipped.append({"id": lid, "reason": "No such lesson."})
            continue
        try:
            (changed if sharing.apply(row, now=now, **_BULK[body.action]) else unchanged).append(lid)
        except sharing.SharingError as e:
            skipped.append({"id": lid, "reason": str(e)})
    listed = await curation.refresh(s, platform.settings, changed) if changed else []
    await s.commit()
    for lid in changed:
        platform.forget_lesson(lid)
    await curation.announce(platform, listed)
    ip = client_ip(request)
    for lid in changed:
        await platform.audit("lesson.moderate", p.user_id, lid, ip, change=body.action, bulk=True)
    return {"action": body.action, "changed": changed, "unchanged": unchanged, "skipped": skipped}


@router.get("/audit")
async def audit(
    p: Auditor, s: DB, action: str | None = None, offset: int = 0, limit: int = 50
) -> dict[str, Any]:
    stmt = select(AuditEvent, User.email).outerjoin(User, User.id == AuditEvent.actor_id)
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action[:40]}%"))
    rows = (
        await s.execute(
            stmt.order_by(AuditEvent.created_at.desc())
            .offset(max(0, offset))
            .limit(max(1, min(limit, 200)) + 1)
        )
    ).all()
    items = [
        {
            "id": e.id,
            "action": e.action,
            "actor": email,
            "target": e.target,
            "ip": e.ip,
            "detail": e.detail,
            "created_at": e.created_at,
        }
        for e, email in rows
    ]
    return {"items": items[:limit], "has_more": len(items) > limit}


@router.get("/system")
async def system(p: Analyst, platform: Plat) -> dict[str, Any]:
    workers: list[bool] = []
    if platform.engine is not None:
        try:
            workers = list(await platform.engine.health())
        except Exception:
            workers = []
    st = platform.app_settings
    return {
        "engine_workers": workers,
        "database": "SQLite" if platform.db.sqlite else platform.db.url.split(":", 1)[0],
        "mail": "SMTP" if platform.mailer.delivers else "outbox (development)",
        "environment": platform.settings.environment,
        "openai_key": bool(st and st.has_openai_key),
        "tts": bool(st and st.animator_tts_enabled),
        "doubts": platform.doubts is not None,
        "started_at": platform.started_at,
        "requests": platform.metrics.snapshot(),
    }
