"""/api/v1/me/...: the learner's dashboard data, statistics (time learned, streaks, a weekly goal,
achievements), library (saved lessons), notes and doubts across lessons; and /api/v1/notifications."""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy import func, select, update

from animator.platform.accounts import preferences_of
from animator.platform.api.lessons import _joined, _rows, _today
from animator.platform.deps import DB, Me
from animator.platform.models import ActivityDay, DoubtLog, Lesson, Note, Notification, Progress, Saved, User

router = APIRouter(prefix="/api/v1", tags=["learning"])

LEARNING_DAY_S = 60  # a day counts towards a streak with at least a minute of learning

ACHIEVEMENTS = [
    ("first-steps", "First steps", "Watch your first minute of a lesson"),
    ("creator", "Creator", "Create your first lesson"),
    ("finisher", "Finisher", "Finish a lesson"),
    ("curious", "Curious mind", "Ask your first doubt"),
    ("note-taker", "Note taker", "Write 5 notes"),
    ("on-a-roll", "On a roll", "Learn 3 days in a row"),
    ("week-warrior", "Week warrior", "Learn 7 days in a row"),
    ("hour", "Hour of learning", "Learn for 60 minutes in total"),
    ("explorer", "Explorer", "Watch a lesson made by someone else"),
    ("scholar", "Scholar", "Finish 10 lessons"),
]


def _streaks(days: dict[str, float], today: dt.date) -> tuple[int, int]:
    learned = sorted(dt.date.fromisoformat(d) for d, sec in days.items() if sec >= LEARNING_DAY_S)
    best = run = 0
    prev: dt.date | None = None
    for d in learned:
        run = run + 1 if prev is not None and (d - prev).days == 1 else 1
        best = max(best, run)
        prev = d
    have = set(learned)
    start = today if today in have else today - dt.timedelta(days=1)
    current = 0
    while start in have:
        current += 1
        start -= dt.timedelta(days=1)
    return current, best


@router.get("/me/stats")
async def stats(p: Me, s: DB, today: str | None = None) -> dict[str, Any]:
    day = dt.date.fromisoformat(_today(today))
    uid = p.user_id
    days = dict(
        (
            await s.execute(select(ActivityDay.day, ActivityDay.seconds).where(ActivityDay.user_id == uid))
        ).all()
    )
    current, best = _streaks(days, day)
    monday = day - dt.timedelta(days=day.weekday())
    week_s = sum(sec for d, sec in days.items() if monday <= dt.date.fromisoformat(d) <= day)
    series = []
    for i in range(83, -1, -1):
        d = (day - dt.timedelta(days=i)).isoformat()
        series.append({"day": d, "minutes": round(days.get(d, 0.0) / 60, 1)})

    async def count(stmt: Any) -> int:
        return int(await s.scalar(stmt) or 0)

    completed = await count(
        select(func.count())
        .select_from(Progress)
        .where(Progress.user_id == uid, Progress.completed_at.is_not(None))
    )
    started = await count(select(func.count()).select_from(Progress).where(Progress.user_id == uid))
    created = await count(
        select(func.count()).select_from(Lesson).where(Lesson.owner_id == uid, Lesson.deleted_at.is_(None))
    )
    doubts = await count(select(func.count()).select_from(DoubtLog).where(DoubtLog.user_id == uid))
    notes = await count(select(func.count()).select_from(Note).where(Note.user_id == uid))
    saved = await count(select(func.count()).select_from(Saved).where(Saved.user_id == uid))
    others = await count(
        select(func.count())
        .select_from(Progress)
        .join(Lesson, Lesson.id == Progress.lesson_id)
        .where(Progress.user_id == uid, Progress.watched_s >= 60, Lesson.owner_id != uid)
    )
    total_s = sum(days.values())
    user = await s.get(User, uid)
    goal = int(preferences_of(user)["weekly_goal_min"]) if user else 60
    unlocked = {
        "first-steps": total_s >= 60,
        "creator": created >= 1,
        "finisher": completed >= 1,
        "curious": doubts >= 1,
        "note-taker": notes >= 5,
        "on-a-roll": best >= 3,
        "week-warrior": best >= 7,
        "hour": total_s >= 3600,
        "explorer": others >= 1,
        "scholar": completed >= 10,
    }
    return {
        "today": day.isoformat(),
        "minutes_total": round(total_s / 60),
        "minutes_today": round(days.get(day.isoformat(), 0.0) / 60, 1),
        "minutes_week": round(week_s / 60, 1),
        "weekly_goal_min": goal,
        "streak": current,
        "best_streak": best,
        "lessons_completed": completed,
        "lessons_started": started,
        "lessons_created": created,
        "doubts_asked": doubts,
        "notes": notes,
        "saved": saved,
        "activity": series,
        "achievements": [
            {"key": k, "title": t, "description": d, "unlocked": unlocked[k]} for k, t, d in ACHIEVEMENTS
        ],
    }


@router.get("/me/learning")
async def learning(p: Me, s: DB) -> dict[str, Any]:
    """The dashboard: lessons to continue, recently finished, recently made."""
    base = _joined(p).where(Lesson.deleted_at.is_(None))
    going = await s.execute(
        base.where(Progress.user_id == p.user_id, Progress.completed_at.is_(None), Progress.percent < 0.95)
        .order_by(Progress.last_watched_at.desc())
        .limit(6)
    )
    done = await s.execute(
        base.where(Progress.user_id == p.user_id, Progress.completed_at.is_not(None))
        .order_by(Progress.completed_at.desc())
        .limit(6)
    )
    made = await s.execute(
        base.where(Lesson.owner_id == p.user_id).order_by(Lesson.created_at.desc()).limit(6)
    )
    return {"continue": _rows(going, p), "completed": _rows(done, p), "created": _rows(made, p)}


@router.get("/me/library")
async def library(p: Me, s: DB, offset: int = 0, limit: int = 24) -> dict[str, Any]:
    limit = max(1, min(limit, 60))
    stmt = (
        _joined(p)
        .where(Saved.user_id == p.user_id, Lesson.deleted_at.is_(None))
        .order_by(Saved.created_at.desc())
        .offset(max(0, offset))
        .limit(limit + 1)
    )
    items = _rows(await s.execute(stmt), p)
    return {"items": items[:limit], "has_more": len(items) > limit}


@router.get("/me/notes")
async def all_notes(p: Me, s: DB, q: str | None = None) -> list[dict[str, Any]]:
    stmt = (
        select(Note, Lesson.title, Lesson.topic)
        .join(Lesson, Lesson.id == Note.lesson_id)
        .where(Note.user_id == p.user_id, Lesson.deleted_at.is_(None))
    )
    if q and q.strip():
        stmt = stmt.where(func.lower(Note.text).like(f"%{q.strip()[:80].lower()}%"))
    rows = (await s.execute(stmt.order_by(Note.updated_at.desc()).limit(300))).all()
    return [
        {
            "id": n.id,
            "lesson_id": n.lesson_id,
            "lesson_title": title or topic,
            "t_s": n.t_s,
            "text": n.text,
            "created_at": n.created_at,
            "updated_at": n.updated_at,
        }
        for n, title, topic in rows
    ]


@router.get("/me/doubts")
async def my_doubts(p: Me, s: DB) -> list[dict[str, Any]]:
    rows = (
        await s.execute(
            select(DoubtLog, Lesson.title, Lesson.topic)
            .outerjoin(Lesson, Lesson.id == DoubtLog.lesson_id)
            .where(DoubtLog.user_id == p.user_id)
            .order_by(DoubtLog.created_at.desc())
            .limit(100)
        )
    ).all()
    return [
        {
            "session_id": d.session_id,
            "lesson_id": d.lesson_id,
            "lesson_title": title or topic or "A lesson",
            "turn": d.turn,
            "question": d.question,
            "title": d.title,
            "kind": d.kind,
            "created_at": d.created_at,
        }
        for d, title, topic in rows
    ]


# ---------------------------------------------------------------------- notifications


@router.get("/notifications")
async def notifications(p: Me, s: DB, limit: int = 30) -> dict[str, Any]:
    rows = (
        await s.scalars(
            select(Notification)
            .where(Notification.user_id == p.user_id)
            .order_by(Notification.created_at.desc())
            .limit(max(1, min(limit, 100)))
        )
    ).all()
    unread = await s.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == p.user_id, Notification.read_at.is_(None))
    )
    return {
        "unread": int(unread or 0),
        "items": [
            {
                "id": n.id,
                "kind": n.kind,
                "title": n.title,
                "body": n.body,
                "link": n.link,
                "read": n.read_at is not None,
                "created_at": n.created_at,
            }
            for n in rows
        ],
    }


@router.post("/notifications/{notification_id}/read", status_code=204)
async def read_one(notification_id: str, p: Me, s: DB) -> Response:
    await s.execute(
        update(Notification)
        .where(
            Notification.id == notification_id,
            Notification.user_id == p.user_id,
            Notification.read_at.is_(None),
        )
        .values(read_at=time.time())
    )
    await s.commit()
    return Response(status_code=204)


@router.post("/notifications/read-all", status_code=204)
async def read_all(p: Me, s: DB) -> Response:
    await s.execute(
        update(Notification)
        .where(Notification.user_id == p.user_id, Notification.read_at.is_(None))
        .values(read_at=time.time())
    )
    await s.commit()
    return Response(status_code=204)
