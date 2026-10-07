"""/api/v1/me: the profile and preferences, password, signed-in devices, a copy of one's data, and
closing the account."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, update

from animator.platform.accounts import clear_cookies, me_out, preferences_of, revoke_sessions
from animator.platform.deps import DB, Me, Plat, client_ip
from animator.platform.models import (
    ActivityDay,
    AuthSession,
    DoubtLog,
    Lesson,
    Note,
    Progress,
    Rating,
    Saved,
    Ticket,
    TicketMessage,
    User,
)
from animator.platform.schemas import DeleteAccountIn, PasswordIn, ProfileIn
from animator.platform.security import hash_password, password_problem, verify_password

router = APIRouter(prefix="/api/v1/me", tags=["account"])


async def _user(s: DB, p: Me) -> User:
    user = await s.get(User, p.user_id)
    if user is None:
        raise HTTPException(401, "Sign in to continue.")
    return user


@router.patch("")
async def update_profile(body: ProfileIn, p: Me, platform: Plat, s: DB) -> dict[str, Any]:
    user = await _user(s, p)
    if body.name is not None:
        user.name = body.name
    if body.bio is not None:
        user.bio = body.bio
    if body.avatar is not None:
        user.avatar = body.avatar
    if body.onboarded is not None:
        user.onboarded = body.onboarded
    if body.preferences is not None:
        merged = {**(user.preferences or {}), **body.preferences.model_dump(exclude_none=True)}
        user.preferences = merged
    await s.commit()
    platform.forget(user_id=user.id)
    return {"user": await me_out(s, platform, user)}


@router.post("/password")
async def change_password(body: PasswordIn, request: Request, p: Me, platform: Plat, s: DB) -> dict[str, Any]:
    user = await _user(s, p)
    wait = platform.limiter.hit(f"password:{user.id}", 10, 3600)
    if wait is not None:
        raise HTTPException(429, "Too many attempts. Try again later.")
    ok, _ = await verify_password(user.password_hash, body.current)
    if not ok:
        raise HTTPException(400, "Your current password is not right.")
    problem = password_problem(body.new, user.email, user.name, platform.settings.password_min_length)
    if problem:
        raise HTTPException(422, problem)
    if body.new == body.current:
        raise HTTPException(422, "Choose a password different from the current one.")
    user.password_hash = await hash_password(body.new)
    signed_out = await revoke_sessions(s, platform, user.id, "password changed", keep=p.session_id)
    await s.commit()
    await platform.audit("password.changed", user.id, user.id, client_ip(request))
    await platform.mailer.send(
        user.email,
        "Your password was changed",
        f"Hi {user.name},\n\nYour password was changed. "
        "If this was not you, reset it now and contact support.",
    )
    return {"ok": True, "signed_out": signed_out}


@router.get("/sessions")
async def sessions(p: Me, s: DB) -> list[dict[str, Any]]:
    rows = (
        await s.scalars(
            select(AuthSession)
            .where(
                AuthSession.user_id == p.user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > time.time(),
            )
            .order_by(AuthSession.last_used_at.desc())
            .limit(50)
        )
    ).all()
    return [
        {
            "id": r.id,
            "user_agent": r.user_agent,
            "ip": r.ip,
            "created_at": r.created_at,
            "last_used_at": r.last_used_at,
            "expires_at": r.expires_at,
            "current": r.id == p.session_id,
        }
        for r in rows
    ]


@router.delete("/sessions/{session_id}", status_code=204)
async def end_session(session_id: str, request: Request, p: Me, platform: Plat, s: DB) -> Response:
    row = await s.get(AuthSession, session_id)
    if row is None or row.user_id != p.user_id:
        raise HTTPException(404, "No such device.")
    if row.revoked_at is None:
        row.revoked_at = time.time()
        row.revoked_reason = "signed out remotely"
        await s.commit()
        platform.forget(session_id=session_id)
        await platform.audit("session.revoked", p.user_id, session_id, client_ip(request))
    return Response(status_code=204)


@router.post("/sessions/revoke-others")
async def end_other_sessions(request: Request, p: Me, platform: Plat, s: DB) -> dict[str, Any]:
    n = await revoke_sessions(s, platform, p.user_id, "signed out remotely", keep=p.session_id)
    await s.commit()
    await platform.audit("session.revoked_others", p.user_id, p.user_id, client_ip(request), count=n)
    return {"signed_out": n}


@router.get("/export")
async def export(p: Me, platform: Plat, s: DB) -> JSONResponse:
    """Everything the platform keeps about the person, as one JSON file."""
    wait = platform.limiter.hit(f"export:{p.user_id}", 5, 3600)
    if wait is not None:
        raise HTTPException(429, "You exported your data recently. Try again later.")
    user = await _user(s, p)

    def rows(objs: Any, *fields: str) -> list[dict[str, Any]]:
        return [{f: getattr(o, f) for f in fields} for o in objs]

    uid = p.user_id
    data = {
        "exported_at": time.time(),
        "profile": {
            "email": user.email,
            "name": user.name,
            "bio": user.bio,
            "role": user.role,
            "created_at": user.created_at,
            "preferences": preferences_of(user),
        },
        "lessons": rows(
            (
                await s.scalars(select(Lesson).where(Lesson.owner_id == uid, Lesson.deleted_at.is_(None)))
            ).all(),
            "id",
            "title",
            "topic",
            "audience",
            "language",
            "duration_min",
            "status",
            "visibility",
            "created_at",
        ),
        "progress": rows(
            (await s.scalars(select(Progress).where(Progress.user_id == uid))).all(),
            "lesson_id",
            "position_s",
            "percent",
            "watched_s",
            "completed_at",
            "last_watched_at",
        ),
        "activity": rows(
            (await s.scalars(select(ActivityDay).where(ActivityDay.user_id == uid))).all(), "day", "seconds"
        ),
        "saved": rows(
            (await s.scalars(select(Saved).where(Saved.user_id == uid))).all(), "lesson_id", "created_at"
        ),
        "notes": rows(
            (await s.scalars(select(Note).where(Note.user_id == uid))).all(),
            "lesson_id",
            "t_s",
            "text",
            "created_at",
        ),
        "ratings": rows(
            (await s.scalars(select(Rating).where(Rating.user_id == uid))).all(),
            "lesson_id",
            "stars",
            "comment",
        ),
        "doubts": rows(
            (await s.scalars(select(DoubtLog).where(DoubtLog.user_id == uid))).all(),
            "lesson_id",
            "question",
            "title",
            "created_at",
        ),
        "tickets": [
            {
                "subject": t.subject,
                "category": t.category,
                "status": t.status,
                "created_at": t.created_at,
                "messages": rows(
                    (await s.scalars(select(TicketMessage).where(TicketMessage.ticket_id == t.id))).all(),
                    "staff",
                    "body",
                    "created_at",
                ),
            }
            for t in (await s.scalars(select(Ticket).where(Ticket.user_id == uid))).all()
        ],
    }
    return JSONResponse(data, headers={"content-disposition": 'attachment; filename="my-animator-data.json"'})


@router.delete("")
async def delete_account(body: DeleteAccountIn, request: Request, p: Me, platform: Plat, s: DB) -> Response:
    user = await _user(s, p)
    ok, _ = await verify_password(user.password_hash, body.password)
    if not ok:
        raise HTTPException(400, "Your password is not right.")
    if user.role == "admin":
        admins = await s.scalar(
            select(func.count()).select_from(User).where(User.role == "admin", User.status == "active")
        )
        if (admins or 0) <= 1:
            raise HTTPException(409, "You are the only admin. Make someone else an admin first.")
    # Their lessons leave the platform with them (the files stay, for the operator).
    await s.execute(
        update(Lesson).where(Lesson.owner_id == user.id).values(deleted_at=time.time(), visibility="private")
    )
    await s.delete(user)
    await s.commit()
    platform.forget(user_id=p.user_id)
    await platform.audit("user.deleted", p.user_id, p.user_id, client_ip(request))
    out = Response(status_code=204)
    clear_cookies(out, platform)
    return out
