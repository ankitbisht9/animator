"""Accounts and sessions: creating people, signing in (a session plus cookies), what /me returns.
Used by the routes and by the command line (`python -m animator.platform create-admin`)."""

from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from animator.platform.core import Platform
from animator.platform.gateway import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from animator.platform.models import AuthSession, DoubtLog, Lesson, OneTimeToken, User
from animator.platform.rbac import perms_of
from animator.platform.security import digest, hash_password, issue_access, random_token

REFRESH_PATH = "/api/v1/auth"
AVATARS = ("crimson", "ruby", "amber", "teal", "indigo", "slate", "plum", "forest")

DEFAULT_PREFERENCES: dict[str, Any] = {
    "language": "English",
    "audience": "high-school student",
    "duration_min": 3,
    "layout": None,
    "interests": [],
    "weekly_goal_min": 60,
    "playback_rate": 1,
    "captions": True,
    "voice": True,
    "autoplay": False,
    "theme": "light",
    "reduced_motion": False,
    "notify_lesson_ready": True,
    "notify_support": True,
    "notify_weekly_summary": False,
}


async def create_user(
    s: AsyncSession, platform: Platform, email: str, password: str, name: str, role: str | None = None
) -> User:
    email = email.strip().lower()
    if role is None:
        first = platform.settings.bootstrap_admin and not await s.scalar(select(User.id).limit(1))
        role = "admin" if first or email in platform.settings.admin_list else "learner"
    user = User(
        email=email,
        name=name.strip()[:80],
        password_hash=await hash_password(password),
        role=role,
        avatar=AVATARS[secrets.randbelow(len(AVATARS))],
        preferences={},
    )
    s.add(user)
    await s.flush()
    return user


def cookie_secure(platform: Platform, request: Request | None) -> bool:
    mode = platform.settings.cookie_secure
    if mode != "auto":
        return mode == "true"
    if platform.settings.production:
        return True
    if request is None:
        return False
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"


async def open_session(
    s: AsyncSession, platform: Platform, user: User, request: Request, remember: bool
) -> tuple[AuthSession, str]:
    """A new signed-in device. Returns the session and its refresh token (plain, for the cookie only)."""
    ps = platform.settings
    token = random_token()
    life = ps.refresh_ttl_days * 86400 if remember else ps.short_session_hours * 3600
    session = AuthSession(
        user_id=user.id,
        refresh_hash=digest(token),
        user_agent=request.headers.get("user-agent", "")[:300],
        ip=str(getattr(request.state, "ip", ""))[:64],
        remember=remember,
        expires_at=time.time() + life,
    )
    s.add(session)
    await s.flush()
    return session, token


def set_cookies(
    response: Response,
    request: Request,
    platform: Platform,
    user: User,
    session: AuthSession,
    refresh: str | None,
) -> None:
    """The access token (every request), the refresh token (only sent to /api/v1/auth) and the CSRF
    token (readable by the page, sent back as a header). Refresh is None when only the access token is
    renewed (another tab already rotated the refresh token)."""
    ps = platform.settings
    secure = cookie_secure(platform, request)
    access = issue_access(ps.secret, user.id, session.id, user.role, ps.access_ttl_min * 60)
    keep = int(session.expires_at - time.time()) if session.remember else None
    common: dict[str, Any] = {"secure": secure, "domain": ps.cookie_domain}
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        max_age=ps.access_ttl_min * 60,
        httponly=True,
        samesite="lax",
        path="/",
        **common,
    )
    if refresh is not None:
        response.set_cookie(
            REFRESH_COOKIE,
            refresh,
            max_age=keep,
            httponly=True,
            samesite="strict",
            path=REFRESH_PATH,
            **common,
        )
    if refresh is not None or not request.cookies.get(CSRF_COOKIE):
        response.set_cookie(
            CSRF_COOKIE,
            secrets.token_urlsafe(24),
            max_age=keep,
            httponly=False,
            samesite="lax",
            path="/",
            **common,
        )


def clear_cookies(response: Response, platform: Platform) -> None:
    domain = platform.settings.cookie_domain
    response.delete_cookie(ACCESS_COOKIE, path="/", domain=domain)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH, domain=domain)
    response.delete_cookie(CSRF_COOKIE, path="/", domain=domain)


async def revoke_sessions(
    s: AsyncSession, platform: Platform, user_id: str, reason: str, keep: str | None = None
) -> int:
    q = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    if keep is not None:
        q = q.where(AuthSession.id != keep)
    result = await s.execute(q.values(revoked_at=time.time(), revoked_reason=reason))
    platform.forget(user_id=user_id)
    return int(getattr(result, "rowcount", 0) or 0)


async def one_time_token(s: AsyncSession, user_id: str, purpose: str, ttl_s: int) -> str:
    """A link token (email verification, password reset); earlier unused ones of the same kind end."""
    await s.execute(
        update(OneTimeToken)
        .where(
            OneTimeToken.user_id == user_id, OneTimeToken.purpose == purpose, OneTimeToken.used_at.is_(None)
        )
        .values(used_at=time.time())
    )
    token = random_token()
    s.add(
        OneTimeToken(
            user_id=user_id, purpose=purpose, token_hash=digest(token), expires_at=time.time() + ttl_s
        )
    )
    return token


async def use_token(s: AsyncSession, token: str, purpose: str) -> OneTimeToken | None:
    row = await s.scalar(
        select(OneTimeToken).where(OneTimeToken.token_hash == digest(token), OneTimeToken.purpose == purpose)
    )
    if row is None or row.used_at is not None or row.expires_at < time.time():
        return None
    row.used_at = time.time()
    return row


def preferences_of(user: User) -> dict[str, Any]:
    return {**DEFAULT_PREFERENCES, **(user.preferences or {})}


async def usage_today(s: AsyncSession, user_id: str) -> dict[str, int]:
    since = time.time() - 86400
    lessons = await s.scalar(
        select(func.count()).select_from(Lesson).where(Lesson.owner_id == user_id, Lesson.created_at > since)
    )
    doubts = await s.scalar(
        select(func.count())
        .select_from(DoubtLog)
        .where(DoubtLog.user_id == user_id, DoubtLog.created_at > since)
    )
    return {"lessons": int(lessons or 0), "doubts": int(doubts or 0)}


async def me_out(s: AsyncSession, platform: Platform, user: User) -> dict[str, Any]:
    ps = platform.settings
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "status": user.status,
        "bio": user.bio,
        "avatar": user.avatar,
        "verified": user.email_verified_at is not None,
        "onboarded": user.onboarded,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
        "preferences": preferences_of(user),
        "permissions": sorted(p.value for p in perms_of(user.role)),
        "limits": {
            "lessons_per_day": ps.lessons_per_day(user.role),
            "doubts_per_day": ps.doubts_per_day(user.role),
            "max_minutes": ps.max_minutes(user.role),
            "require_verified": ps.require_verified_email,
        },
        "usage": await usage_today(s, user.id),
    }
