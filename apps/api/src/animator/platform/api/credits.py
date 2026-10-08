"""/api/v1/admin/usage: credits and cost (ADR-053).

What the platform spent (AI and voice, making lessons and learning), today, this week, this month and
all time; per person, with their monthly credits (an admin can change them or give credits back); per
lesson; and the ledger itself as a CSV file. Reading needs `usage:read`; changing someone's credits
needs `credits:manage` (both are admins' today; a finance role would be one line in rbac.py).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, select

from animator.platform import usage
from animator.platform.deps import DB, CreditManager, Plat, UsageReader, client_ip
from animator.platform.models import DoubtLog, Lesson, UsageEntry, User
from animator.platform.schemas import AdjustmentIn, CreditsIn

router = APIRouter(prefix="/api/v1/admin/usage", tags=["admin: credits and cost"])

Period = Literal["today", "week", "month", "total"]
CREATE = ("lesson_ai", "lesson_voice")
LEARN = ("doubt_ai", "doubt_voice", "transcription")


def _window(ck: usage.Clock, period: str) -> list[Any]:
    since = ck.since(period)
    return [] if since is None else [UsageEntry.created_at >= since]


def _allowance_out(user: User, used_milli: int, settings: Any) -> dict[str, Any]:
    limit = usage.limit_milli(settings, user.role, user.credit_mode, user.credit_limit)
    return {
        "mode": user.credit_mode,
        "limit": None if limit is None else usage.credits(limit),
        "role_limit": settings.monthly_credits(user.role) or None,
        "used": usage.credits(used_milli),
        "percent": None if not limit else round(used_milli / limit, 4),
        "exhausted": limit is not None and used_milli >= limit,
    }


@router.get("/summary")
async def summary(p: UsageReader, platform: Plat, s: DB, days: int = 30) -> dict[str, Any]:
    ps = platform.settings
    ck = usage.clock(ps)
    sums = await usage.period_sums(s, ck)
    month = ck.since("month")
    assert month is not None
    per_person = (
        await s.execute(
            select(
                User.id,
                User.name,
                User.email,
                User.role,
                User.credit_mode,
                User.credit_limit,
                func.sum(UsageEntry.credits_milli),
                func.sum(UsageEntry.cost_micros),
            )
            .join(UsageEntry, UsageEntry.user_id == User.id)
            .where(UsageEntry.created_at >= month)
            .group_by(User.id, User.name, User.email, User.role, User.credit_mode, User.credit_limit)
        )
    ).all()
    over = near = 0
    for _id, _name, _email, role, mode, limit, milli, _cost in per_person:
        cap = usage.limit_milli(ps, role, mode, limit)
        if cap is None:
            continue
        if int(milli or 0) >= cap:
            over += 1
        elif cap > 0 and int(milli or 0) >= 0.8 * cap:
            near += 1
    blocked = await s.scalar(select(func.count()).select_from(User).where(User.credit_mode == "blocked"))
    top = sorted(per_person, key=lambda r: int(r[7] or 0), reverse=True)[:5]
    made = await s.scalar(
        select(func.count(func.distinct(UsageEntry.lesson_id))).where(
            UsageEntry.created_at >= month, UsageEntry.kind.in_(CREATE)
        )
    )
    answers = await s.scalar(
        select(func.count()).where(UsageEntry.created_at >= month, UsageEntry.kind == "doubt_ai")
    )
    unnamed = await s.scalar(
        select(func.coalesce(func.sum(UsageEntry.cost_micros), 0)).where(UsageEntry.user_id.is_(None))
    )
    st = platform.app_settings
    configured = [
        str(v) for v in ((st.animator_tts_provider, st.animator_tts_provider_hinglish) if st else ()) if v
    ]
    charged = (
        await s.scalars(
            select(UsageEntry.note)
            .where(UsageEntry.kind.in_(("lesson_voice", "doubt_voice")), UsageEntry.note != "")
            .distinct()
            .limit(12)
        )
    ).all()
    month_kinds = sums["month"]
    create_cost = sum(month_kinds.get(k, (0, 0, 0))[0] for k in CREATE)
    doubt_cost = sum(month_kinds.get(k, (0, 0, 0))[0] for k in ("doubt_ai", "doubt_voice"))
    return {
        "timezone": ps.zone_name,
        "credit_usd": ps.credit_usd,
        "now": ck.now,
        "starts": {name: ck.since(name) for name in ("today", "week", "month")},
        "resets_at": ck.next_month,
        "periods": {name: usage.shape(sums[name]) for name in usage.PERIODS},
        "daily": await usage.daily(s, ck, days),
        "people": {
            "spending_this_month": len(per_person),
            "over_limit": over,
            "near_limit": near,
            "blocked": int(blocked or 0),
        },
        "top": [
            {
                "id": uid,
                "name": name,
                "email": email,
                "credits": usage.credits(int(milli or 0)),
                "cost_usd": usage.usd(int(cost or 0)),
            }
            for uid, name, email, _role, _mode, _limit, milli, cost in top
        ],
        "averages": {
            "lesson_usd": usage.usd(create_cost // made) if made else None,
            "doubt_usd": usage.usd(doubt_cost // answers) if answers else None,
        },
        "unattributed_usd": usage.usd(int(unnamed or 0)),
        "allowances": {role: ps.monthly_credits(role) or None for role in ("learner", "educator", "admin")},
        "prices": {
            # The voices in use (configured now, or charged before) and the price each is counted at.
            "voices": [
                {
                    "provider": name,
                    "usd_per_million_chars": ps.voice_price(name),
                    "configured": name in configured,
                }
                for name in dict.fromkeys([*configured, *charged])
            ],
            "transcribe_usd_per_min": ps.transcribe_usd_per_min,
        },
    }


@router.get("/people")
async def people(
    p: UsageReader,
    platform: Plat,
    s: DB,
    period: Period = "month",
    q: str | None = None,
    role: str | None = None,
    sort: Literal["credits", "cost", "used", "name", "recent"] = "credits",
    active: bool = False,
    offset: int = 0,
    limit: int = 25,
) -> dict[str, Any]:
    """Everyone (or only those who spent in the period), with what they spent and their credits."""
    ps = platform.settings
    ck = usage.clock(ps)
    limit = max(1, min(limit, 100))
    month = ck.since("month")
    spent = (
        select(
            UsageEntry.user_id.label("uid"),
            func.sum(UsageEntry.cost_micros).label("cost"),
            func.sum(UsageEntry.credits_milli).label("milli"),
            func.sum(case((UsageEntry.kind.in_(CREATE), UsageEntry.cost_micros), else_=0)).label(
                "create_cost"
            ),
            func.sum(case((UsageEntry.kind.in_(LEARN), UsageEntry.cost_micros), else_=0)).label("learn_cost"),
            func.max(UsageEntry.created_at).label("last"),
        )
        .where(UsageEntry.user_id.is_not(None), *_window(ck, period))
        .group_by(UsageEntry.user_id)
        .subquery()
    )
    used = (
        select(UsageEntry.user_id.label("uid"), func.sum(UsageEntry.credits_milli).label("used"))
        .where(UsageEntry.user_id.is_not(None), UsageEntry.created_at >= month)
        .group_by(UsageEntry.user_id)
        .subquery()
    )
    stmt = (
        select(
            User,
            spent.c.cost,
            spent.c.milli,
            spent.c.create_cost,
            spent.c.learn_cost,
            spent.c.last,
            used.c.used,
        )
        .outerjoin(spent, spent.c.uid == User.id)
        .outerjoin(used, used.c.uid == User.id)
    )
    if q and q.strip():
        term = f"%{q.strip()[:80].lower()}%"
        stmt = stmt.where(func.lower(User.email).like(term) | func.lower(User.name).like(term))
    if role in ("learner", "educator", "admin"):
        stmt = stmt.where(User.role == role)
    if active:
        stmt = stmt.where(spent.c.uid.is_not(None))
    total = await s.scalar(select(func.count()).select_from(stmt.with_only_columns(User.id).subquery()))
    order = {
        "credits": [func.coalesce(spent.c.milli, 0).desc()],
        "cost": [func.coalesce(spent.c.cost, 0).desc()],
        "used": [func.coalesce(used.c.used, 0).desc()],
        "name": [func.lower(User.name).asc()],
        "recent": [func.coalesce(spent.c.last, 0).desc()],
    }[sort]
    rows = (
        await s.execute(stmt.order_by(*order, User.created_at.desc()).offset(max(0, offset)).limit(limit))
    ).all()
    ids = [r[0].id for r in rows]
    lessons: dict[str, int] = {}
    doubts: dict[str, int] = {}
    since = ck.since(period)
    if ids:
        lq = select(Lesson.owner_id, func.count()).where(Lesson.owner_id.in_(ids))
        dq = select(DoubtLog.user_id, func.count()).where(DoubtLog.user_id.in_(ids))
        if since is not None:
            lq = lq.where(Lesson.created_at >= since)
            dq = dq.where(DoubtLog.created_at >= since)
        lessons = {k: int(v) for k, v in (await s.execute(lq.group_by(Lesson.owner_id))).all() if k}
        doubts = {k: int(v) for k, v in (await s.execute(dq.group_by(DoubtLog.user_id))).all() if k}
    return {
        "total": int(total or 0),
        "period": period,
        "items": [
            {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "role": user.role,
                "status": user.status,
                "avatar": user.avatar,
                "credits": _allowance_out(user, int(month_used or 0), ps),
                "spent": {
                    "cost_usd": usage.usd(int(cost or 0)),
                    "credits": usage.credits(int(milli or 0)),
                    "create_usd": usage.usd(int(create_cost or 0)),
                    "learn_usd": usage.usd(int(learn_cost or 0)),
                    "last_at": last,
                },
                "lessons": lessons.get(user.id, 0),
                "doubts": doubts.get(user.id, 0),
            }
            for user, cost, milli, create_cost, learn_cost, last, month_used in rows
        ],
    }


@router.get("/people/{user_id}")
async def person(user_id: str, p: UsageReader, platform: Plat, s: DB, days: int = 30) -> dict[str, Any]:
    ps = platform.settings
    user = await s.get(User, user_id)
    if user is None:
        raise HTTPException(404, "No such person.")
    ck = usage.clock(ps)
    theirs = UsageEntry.user_id == user.id
    sums = await usage.period_sums(s, ck, theirs)
    allowance = await usage.allowance(s, ps, user, ck.now)
    by_lesson = (
        await s.execute(
            select(
                UsageEntry.lesson_id,
                Lesson.title,
                Lesson.deleted_at,
                func.sum(UsageEntry.cost_micros),
                func.sum(UsageEntry.credits_milli),
                func.sum(case((UsageEntry.kind.in_(CREATE), UsageEntry.cost_micros), else_=0)),
                func.max(UsageEntry.created_at),
            )
            .outerjoin(Lesson, Lesson.id == UsageEntry.lesson_id)
            .where(theirs, UsageEntry.lesson_id.is_not(None))
            .group_by(UsageEntry.lesson_id, Lesson.title, Lesson.deleted_at)
            .order_by(func.sum(UsageEntry.cost_micros).desc())
            .limit(20)
        )
    ).all()
    actor = User.__table__.alias("actor")
    recent = (
        await s.execute(
            select(UsageEntry, Lesson.title, actor.c.name)
            .outerjoin(Lesson, Lesson.id == UsageEntry.lesson_id)
            .outerjoin(actor, actor.c.id == UsageEntry.actor_id)
            .where(theirs)
            .order_by(UsageEntry.created_at.desc())
            .limit(50)
        )
    ).all()
    return {
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "status": user.status,
            "avatar": user.avatar,
            "created_at": user.created_at,
        },
        "credits": {**allowance.out(), "role_limit": ps.monthly_credits(user.role) or None},
        "periods": {name: usage.shape(sums[name]) for name in usage.PERIODS},
        "daily": await usage.daily(s, ck, days, theirs),
        "lessons": [
            {
                "id": lesson_id,
                "title": title or "(a lesson no longer in the catalog)",
                "removed": deleted is not None,
                "cost_usd": usage.usd(int(cost or 0)),
                "credits": usage.credits(int(milli or 0)),
                "made": int(create or 0) != 0,
                "last_at": last,
            }
            for lesson_id, title, deleted, cost, milli, create, last in by_lesson
        ],
        "recent": [
            {
                "id": e.id,
                "kind": e.kind,
                "lesson_id": e.lesson_id,
                "lesson_title": title,
                "cost_usd": usage.usd(e.cost_micros),
                "credits": usage.credits(e.credits_milli),
                "estimated": e.estimated,
                "quantity": e.quantity,
                "note": e.note,
                "by": by,
                "at": e.created_at,
            }
            for e, title, by in recent
        ],
    }


def _credits_text(mode: str, limit: int | None, role_limit: int) -> str:
    if mode == "unlimited":
        return "You have no monthly credit limit."
    if mode == "blocked":
        return "Your account cannot use credits for now. You can still watch lessons."
    if mode == "custom" and limit is not None:
        return f"You now have {limit:,} credits a month."
    return (
        f"Your role's {role_limit:,} credits a month apply."
        if role_limit
        else "You have no monthly credit limit."
    )


@router.put("/people/{user_id}/credits")
async def set_credits(
    user_id: str, body: CreditsIn, request: Request, p: CreditManager, platform: Plat, s: DB
) -> dict[str, Any]:
    ps = platform.settings
    user = await s.get(User, user_id)
    if user is None:
        raise HTTPException(404, "No such person.")
    before = {"mode": user.credit_mode, "limit": user.credit_limit or None}
    user.credit_mode = body.mode
    user.credit_limit = body.limit or 0
    await s.commit()
    after = {"mode": body.mode, "limit": body.limit}
    if before != after:
        await platform.audit(
            "credits.set", p.user_id, user.id, client_ip(request), before=before, after=after
        )
        await platform.notify(
            user.id,
            "credits",
            "Your monthly credits changed",
            _credits_text(body.mode, body.limit, ps.monthly_credits(user.role)),
            "/settings/credits",
        )
    return {**(await usage.allowance(s, ps, user)).out(), "role_limit": ps.monthly_credits(user.role) or None}


@router.post("/people/{user_id}/adjustments", status_code=201)
async def adjust(
    user_id: str, body: AdjustmentIn, request: Request, p: CreditManager, platform: Plat, s: DB
) -> dict[str, Any]:
    """Credits given back (positive) or charged (negative). No dollars move: it changes only what the
    person has left this month."""
    ps = platform.settings
    user = await s.get(User, user_id)
    if user is None:
        raise HTTPException(404, "No such person.")
    if platform.limiter.hit(f"adjust:{p.user_id}", 60, 3600) is not None:
        raise HTTPException(429, "Many adjustments in a short time. Try again in a while.")
    milli = round(body.credits * 1000)
    await usage.post(s, ps, "adjustment", user.id, credits_milli=-milli, note=body.note, actor_id=p.user_id)
    await s.commit()
    await platform.audit(
        "credits.adjust", p.user_id, user.id, client_ip(request), credits=body.credits, note=body.note
    )
    amount = usage.show_credits(abs(milli))
    title = f"{amount} credits were given back to you" if milli > 0 else f"{amount} credits were charged"
    await platform.notify(user.id, "credits", title, body.note, "/settings/credits")
    return {**(await usage.allowance(s, ps, user)).out(), "role_limit": ps.monthly_credits(user.role) or None}


@router.get("/lessons")
async def lessons(
    p: UsageReader,
    platform: Plat,
    s: DB,
    period: Period = "month",
    q: str | None = None,
    offset: int = 0,
    limit: int = 25,
) -> dict[str, Any]:
    """Lessons by what they cost: making them (their owner) and the doubts asked on them (learners)."""
    ck = usage.clock(platform.settings)
    limit = max(1, min(limit, 100))
    agg = (
        select(
            UsageEntry.lesson_id.label("lid"),
            func.sum(UsageEntry.cost_micros).label("cost"),
            func.sum(UsageEntry.credits_milli).label("milli"),
            func.sum(case((UsageEntry.kind.in_(CREATE), UsageEntry.cost_micros), else_=0)).label(
                "create_cost"
            ),
            func.sum(case((UsageEntry.kind.in_(LEARN), UsageEntry.cost_micros), else_=0)).label("learn_cost"),
            func.sum(case((UsageEntry.kind == "doubt_ai", 1), else_=0)).label("answers"),
            func.max(UsageEntry.created_at).label("last"),
        )
        .where(UsageEntry.lesson_id.is_not(None), *_window(ck, period))
        .group_by(UsageEntry.lesson_id)
        .subquery()
    )
    stmt = (
        select(agg, Lesson.title, Lesson.topic, Lesson.subject, Lesson.status, Lesson.deleted_at, User.name)
        .outerjoin(Lesson, Lesson.id == agg.c.lid)
        .outerjoin(User, User.id == Lesson.owner_id)
    )
    if q and q.strip():
        term = f"%{q.strip()[:80].lower()}%"
        stmt = stmt.where(func.lower(Lesson.title).like(term) | func.lower(Lesson.topic).like(term))
    total = await s.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        await s.execute(stmt.order_by(agg.c.cost.desc(), agg.c.lid).offset(max(0, offset)).limit(limit))
    ).all()
    return {
        "total": int(total or 0),
        "period": period,
        "items": [
            {
                "id": r.lid,
                "title": r.title or r.topic or "(a lesson no longer in the catalog)",
                "subject": r.subject,
                "status": r.status,
                "removed": r.deleted_at is not None,
                "owner": r.name,
                "cost_usd": usage.usd(int(r.cost or 0)),
                "credits": usage.credits(int(r.milli or 0)),
                "create_usd": usage.usd(int(r.create_cost or 0)),
                "learn_usd": usage.usd(int(r.learn_cost or 0)),
                "answers": int(r.answers or 0),
                "last_at": r.last,
            }
            for r in rows
        ],
    }


_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _cell(text: str | None) -> str:
    """Text a spreadsheet must not run as a formula (CSV injection): a leading = + - @ is quoted."""
    text = text or ""
    return f"'{text}" if text.startswith(_FORMULA) else text


@router.get("/export.csv")
async def export_csv(
    request: Request, p: UsageReader, platform: Plat, period: Period = "month"
) -> StreamingResponse:
    """The ledger for a period, one row per entry, oldest first, times in the platform's time zone."""
    ps = platform.settings
    if platform.limiter.hit(f"usage-export:{p.user_id}", 20, 3600) is not None:
        raise HTTPException(429, "Many exports in a short time. Try again in a while.")
    ck = usage.clock(ps)
    stmt = (
        select(UsageEntry, User.name, User.email, Lesson.title)
        .outerjoin(User, User.id == UsageEntry.user_id)
        .outerjoin(Lesson, Lesson.id == UsageEntry.lesson_id)
        .where(*_window(ck, period))
        .order_by(UsageEntry.created_at, UsageEntry.id)
    )
    await platform.audit("usage.export", p.user_id, period, client_ip(request))
    header = [
        "time",
        "day",
        "kind",
        "area",
        "person",
        "email",
        "lesson_id",
        "lesson",
        "cost_usd",
        "credits",
        "estimated",
        "quantity",
        "note",
    ]

    def line(values: list[Any]) -> str:
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\r\n").writerow(values)
        return buf.getvalue()

    async def rows() -> AsyncIterator[str]:
        yield "﻿" + line(header)  # the BOM tells spreadsheets it is UTF-8
        # Its own session: the request's one closes when the response starts streaming.
        async with platform.db.session() as session:
            result = await session.stream(stmt.execution_options(yield_per=1000))
            async for e, name, email, title in result:
                when = dt.datetime.fromtimestamp(e.created_at, ps.zone)
                if when.tzinfo is None:
                    when = when.astimezone()
                yield line(
                    [
                        when.isoformat(timespec="seconds"),
                        when.date().isoformat(),
                        e.kind,
                        usage.KINDS.get(e.kind, ""),
                        _cell(name if e.user_id else "No account"),
                        _cell(email),
                        e.lesson_id or "",
                        _cell(title),
                        f"{e.cost_micros / 1_000_000:.6f}",
                        f"{e.credits_milli / 1000:.3f}",
                        "yes" if e.estimated else "no",
                        e.quantity,
                        _cell(e.note),
                    ]
                )

    stamp = ck.today.isoformat()
    return StreamingResponse(
        rows(),
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": f'attachment; filename="animator-usage-{period}-{stamp}.csv"'},
    )
