"""/api/v1/auth: sign up, sign in, refresh, sign out, password reset, email verification.

Brute force: per-address and per-email rate limits, and an account lock after repeated failures.
Enumeration: sign-in failures and password-reset requests answer the same whether or not the email
has an account (sign-up must say that an email is taken; it is rate limited).
"""

from __future__ import annotations

import math
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from animator.platform.accounts import (
    clear_cookies,
    create_user,
    me_out,
    one_time_token,
    open_session,
    revoke_sessions,
    set_cookies,
    use_token,
)
from animator.platform.deps import DB, Me, Plat, client_ip
from animator.platform.gateway import REFRESH_COOKIE
from animator.platform.models import AuthSession, User
from animator.platform.schemas import ForgotIn, LoginIn, RegisterIn, ResetIn, TokenIn
from animator.platform.security import digest, hash_password, password_problem, random_token, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# A refresh token presented again within this many seconds of being replaced is another tab racing
# the first, not a thief: it gets an access token, and the session lives on.
REFRESH_GRACE_S = 30
VERIFY_TTL_S = 3 * 86400
RESET_TTL_S = 3600


def _limit(platform: Any, key: str, limit: int, window: float, message: str) -> None:
    wait = platform.limiter.hit(key, limit, window)
    if wait is not None:
        raise HTTPException(429, message, headers={"retry-after": str(math.ceil(wait))})


async def _send_verification(platform: Any, s: DB, user: User) -> None:
    token = await one_time_token(s, user.id, "verify_email", VERIFY_TTL_S)
    await s.commit()
    link = f"{platform.settings.public_base_url.rstrip('/')}/verify-email?token={token}"
    await platform.mailer.send(
        user.email,
        "Confirm your email",
        f"Hi {user.name},\n\nConfirm your email to keep your account safe:\n{link}\n\n"
        "The link works for 3 days. If you did not sign up, ignore this mail.",
    )


@router.post("/register", status_code=201)
async def register(
    body: RegisterIn, request: Request, response: Response, platform: Plat, s: DB
) -> dict[str, Any]:
    ps = platform.settings
    email = str(body.email).lower()
    if not ps.may_sign_up(email):
        raise HTTPException(403, "Sign-ups are closed. Ask an administrator for an account.")
    ip = client_ip(request)
    _limit(platform, f"register:{ip}", 10, 3600, "Too many sign-ups from this network. Try again later.")
    problem = password_problem(body.password, email, body.name, ps.password_min_length)
    if problem:
        raise HTTPException(422, problem)
    if await s.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    user = await create_user(s, platform, email, body.password, body.name)
    session, refresh = await open_session(s, platform, user, request, body.remember)
    user.last_login_at = time.time()
    await s.commit()
    set_cookies(response, request, platform, user, session, refresh)
    await platform.audit("user.register", user.id, user.id, ip, role=user.role)
    await platform.notify(
        user.id,
        "welcome",
        f"Welcome, {user.name.split()[0]}!",
        "Create your first lesson, or explore what others have made.",
        "/create",
    )
    if user.role == "admin" and getattr(platform, "catalog", None) is not None:
        await platform.catalog.claim_orphans(user.id)
    await _send_verification(platform, s, user)
    return {"user": await me_out(s, platform, user)}


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, platform: Plat, s: DB) -> dict[str, Any]:
    ps = platform.settings
    ip = client_ip(request)
    email = str(body.email).lower()
    _limit(platform, f"login-ip:{ip}", 30, 600, "Too many sign-in attempts. Wait a few minutes.")
    _limit(platform, f"login-email:{email}", 10, 600, "Too many sign-in attempts. Wait a few minutes.")
    user = await s.scalar(select(User).where(User.email == email))
    now = time.time()
    if user is not None and user.locked_until and user.locked_until > now:
        minutes = math.ceil((user.locked_until - now) / 60)
        raise HTTPException(
            429, f"Too many failed attempts. Try again in {minutes} minute(s), or reset your password."
        )
    ok, rehash = await verify_password(user.password_hash if user else None, body.password)
    if user is None or not ok:
        if user is not None:
            user.failed_logins += 1
            if user.failed_logins >= ps.max_failed_logins:
                user.locked_until = now + ps.lockout_minutes * 60
                user.failed_logins = 0
                await platform.audit("user.locked", user.id, user.id, ip)
            await s.commit()
        await platform.audit("login.failed", user.id if user else None, email[:64], ip)
        raise HTTPException(401, "Email or password is incorrect.")
    if user.status != "active":
        raise HTTPException(403, "This account is suspended. Contact support if you think this is a mistake.")
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    if rehash:
        user.password_hash = rehash
    session, refresh = await open_session(s, platform, user, request, body.remember)
    await s.commit()
    set_cookies(response, request, platform, user, session, refresh)
    await platform.audit("login.ok", user.id, user.id, ip)
    return {"user": await me_out(s, platform, user)}


@router.post("/refresh")
async def refresh(request: Request, response: Response, platform: Plat, s: DB) -> dict[str, Any]:
    token = request.cookies.get(REFRESH_COOKIE)
    _limit(platform, f"refresh:{client_ip(request)}", 120, 60, "Too many requests.")
    if not token:
        raise HTTPException(401, "Not signed in.")
    h = digest(token)
    now = time.time()
    session = await s.scalar(select(AuthSession).where(AuthSession.refresh_hash == h))
    rotated = True
    if session is None:
        session = await s.scalar(select(AuthSession).where(AuthSession.previous_hash == h))
        if session is None:
            clear_cookies(response, platform)
            raise HTTPException(401, "Your session has ended. Sign in again.")
        if session.revoked_at is None and now - session.last_used_at <= REFRESH_GRACE_S:
            rotated = False  # another tab refreshed a moment ago
        else:
            if session.revoked_at is None:
                session.revoked_at = now
                session.revoked_reason = "reused"
                await s.commit()
                platform.forget(session_id=session.id)
                await platform.audit("session.reused", session.user_id, session.id, client_ip(request))
            clear_cookies(response, platform)
            raise HTTPException(401, "Your session has ended. Sign in again.")
    if session.revoked_at is not None or session.expires_at < now:
        clear_cookies(response, platform)
        raise HTTPException(401, "Your session has ended. Sign in again.")
    user = await s.get(User, session.user_id)
    if user is None or user.status != "active":
        clear_cookies(response, platform)
        raise HTTPException(401, "Your session has ended. Sign in again.")
    new_token: str | None = None
    if rotated:
        new_token = random_token()
        session.previous_hash = session.refresh_hash
        session.refresh_hash = digest(new_token)
        session.last_used_at = now
        session.ip = client_ip(request)[:64]
    await s.commit()
    set_cookies(response, request, platform, user, session, new_token)
    return {"user": await me_out(s, platform, user)}


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, platform: Plat, s: DB) -> Response:
    token = request.cookies.get(REFRESH_COOKIE)
    p = getattr(request.state, "principal", None)
    session = None
    if token:
        session = await s.scalar(select(AuthSession).where(AuthSession.refresh_hash == digest(token)))
    if session is None and p is not None:
        session = await s.get(AuthSession, p.session_id)
    if session is not None and session.revoked_at is None:
        session.revoked_at = time.time()
        session.revoked_reason = "signed out"
        await s.commit()
        platform.forget(session_id=session.id)
        await platform.audit("logout", session.user_id, session.id, client_ip(request))
    out = Response(status_code=204)
    clear_cookies(out, platform)
    return out


@router.get("/me")
async def me(p: Me, platform: Plat, s: DB) -> dict[str, Any]:
    user = await s.get(User, p.user_id)
    if user is None:
        raise HTTPException(401, "Sign in to continue.")
    return {"user": await me_out(s, platform, user)}


@router.post("/password/forgot", status_code=202)
async def forgot(body: ForgotIn, request: Request, platform: Plat, s: DB) -> dict[str, Any]:
    email = str(body.email).lower()
    _limit(platform, f"forgot-ip:{client_ip(request)}", 10, 3600, "Too many requests. Try again later.")
    _limit(platform, f"forgot-email:{email}", 3, 3600, "A reset link was sent recently. Check your inbox.")
    user = await s.scalar(select(User).where(User.email == email))
    if user is not None and user.status == "active":
        token = await one_time_token(s, user.id, "reset_password", RESET_TTL_S)
        await s.commit()
        link = f"{platform.settings.public_base_url.rstrip('/')}/reset-password?token={token}"
        await platform.mailer.send(
            user.email,
            "Reset your password",
            f"Hi {user.name},\n\nChoose a new password here:\n{link}\n\n"
            "The link works for one hour. If you did not ask for it, ignore this mail: your password stays.",
        )
        await platform.audit("password.reset_requested", user.id, user.id, client_ip(request))
    return {"ok": True, "message": "If that email has an account, a reset link is on its way."}


@router.post("/password/reset")
async def reset(body: ResetIn, request: Request, platform: Plat, s: DB) -> dict[str, Any]:
    _limit(platform, f"reset:{client_ip(request)}", 20, 3600, "Too many attempts. Try again later.")
    row = await use_token(s, body.token, "reset_password")
    if row is None:
        raise HTTPException(400, "This reset link is invalid or has expired. Ask for a new one.")
    user = await s.get(User, row.user_id)
    if user is None:
        raise HTTPException(400, "This reset link is invalid or has expired. Ask for a new one.")
    problem = password_problem(body.password, user.email, user.name, platform.settings.password_min_length)
    if problem:
        raise HTTPException(422, problem)
    user.password_hash = await hash_password(body.password)
    user.failed_logins = 0
    user.locked_until = None
    if user.email_verified_at is None:
        user.email_verified_at = time.time()  # the link proved the inbox
    await revoke_sessions(s, platform, user.id, "password reset")
    await s.commit()
    await platform.audit("password.reset", user.id, user.id, client_ip(request))
    await platform.mailer.send(
        user.email,
        "Your password was changed",
        f"Hi {user.name},\n\nYour password was just reset and every device was signed out. "
        "If this was not you, reset it again now and contact support.",
    )
    return {"ok": True}


@router.post("/verify-email")
async def verify_email(body: TokenIn, request: Request, platform: Plat, s: DB) -> dict[str, Any]:
    _limit(platform, f"verify:{client_ip(request)}", 30, 3600, "Too many attempts. Try again later.")
    row = await use_token(s, body.token, "verify_email")
    if row is None:
        raise HTTPException(400, "This confirmation link is invalid or has expired.")
    user = await s.get(User, row.user_id)
    if user is not None and user.email_verified_at is None:
        user.email_verified_at = time.time()
    await s.commit()
    platform.forget(user_id=row.user_id)
    return {"ok": True}


@router.post("/verify-email/resend", status_code=202)
async def resend_verification(p: Me, platform: Plat, s: DB) -> dict[str, Any]:
    _limit(
        platform, f"resend:{p.user_id}", 3, 3600, "A confirmation mail was sent recently. Check your inbox."
    )
    user = await s.get(User, p.user_id)
    if user is not None and user.email_verified_at is None:
        await _send_verification(platform, s, user)
    return {"ok": True}
