"""The API gateway (ADR-051): one ASGI layer in front of every route.

For each request, in order:
1. a request id (kept from `X-Request-ID` when well formed), on the response and in the logs;
2. body size limits (413);
3. cross-site checks on state-changing requests: the `Origin` must be ours, and a cookie-signed
   request must carry the CSRF token (double-submit) (403);
4. who is asking: the access token from the cookie (browser) or `Authorization: Bearer` (scripts);
5. a rate limit per person (or per address when signed out) (429);
6. the access rules of the routes that existed before accounts (lessons, doubts, audio, config),
   kept in one table here so those routes stay exactly as they were: a lesson is read by its owner,
   by anyone when shared, and by admins; changed only by its owner and admins; anything not in the
   table is for admins;
7. security headers on every response, `no-store` on account data;
8. metrics and an access log line; an unexpected error becomes a 500 naming the request id.

Pure ASGI (not BaseHTTPMiddleware) so event streams flow through unbuffered.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from animator.platform.core import Platform, Principal
from animator.platform.models import DoubtLog, Lesson
from animator.platform.rbac import Perm

log = logging.getLogger("animator.gateway")

ACCESS_COOKIE = "anim_at"
REFRESH_COOKIE = "anim_rt"
CSRF_COOKIE = "anim_csrf"
CSRF_HEADER = "x-csrf-token"

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
GEN = r"g_[0-9a-f]{12}"
DOUBT = r"d_[0-9a-f]{12}"
_RID = re.compile(r"^[A-Za-z0-9_.-]{6,64}$")
# Sign-in and recovery happen before there is a session (and a CSRF token) to check.
CSRF_EXEMPT = re.compile(
    r"^/api/v1/auth/(login|register|refresh|logout|password/forgot|password/reset|verify-email)$"
)

Hook = Callable[[bytes], Coroutine[Any, Any, None]]


class _TooLarge(Exception):
    pass


@dataclass(frozen=True)
class Rule:
    methods: frozenset[str]
    pattern: re.Pattern[str]
    access: str  # public | signed_in | perm | reader | owner | doubt_ask | doubt_read | docs
    perm: Perm | None = None
    hook: str | None = None


def _rule(methods: str, pattern: str, access: str, perm: Perm | None = None, hook: str | None = None) -> Rule:
    return Rule(frozenset(methods.split("|")), re.compile(pattern), access, perm, hook)


# The routes that existed before accounts. /api/v1 checks itself (FastAPI dependencies).
LEGACY: list[Rule] = [
    _rule("GET|HEAD", r"^/api/health$", "public"),
    _rule("GET|HEAD", r"^/api/tts/[A-Za-z0-9_.-]+$", "public"),  # named by content, cached forever
    _rule("GET", r"^/api/config$", "signed_in"),
    _rule("GET", r"^/api/generations$", "perm", Perm.DEBUG),
    _rule("POST", r"^/api/generations$", "perm", Perm.DEBUG, hook="lesson"),
    _rule("GET", rf"^/api/generations/(?P<gid>{GEN})$", "owner"),
    _rule("GET", rf"^/api/generations/(?P<gid>{GEN})/events$", "reader"),
    _rule("POST", rf"^/api/generations/(?P<gid>{GEN})/(cancel|resume|playhead)$", "owner"),
    _rule("GET", rf"^/api/generations/(?P<gid>{GEN})/calls$", "perm", Perm.DEBUG),
    _rule("GET|POST", rf"^/api/generations/(?P<gid>{GEN})/export(/video)?$", "owner"),
    _rule("GET", r"^/api/doubts/(config|phrases)$", "signed_in"),
    _rule("POST", r"^/api/doubts/transcribe$", "perm", Perm.DOUBT_ASK),
    _rule("POST", rf"^/api/doubts/(?P<gid>{GEN})$", "doubt_ask", hook="doubt"),
    _rule("GET", rf"^/api/doubts/(?P<gid>{GEN})/(?P<sid>{DOUBT})$", "doubt_read"),
    _rule("GET|HEAD", r"^/(docs|redoc|openapi\.json)(/.*)?$", "docs", Perm.DEBUG),
]

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), geolocation=(), payment=(), microphone=(self)"),
]


def _deny(status: int, detail: str, rid: str, retry_after: float | None = None) -> Response:
    headers = {"x-request-id": rid}
    if retry_after is not None:
        headers["retry-after"] = str(int(retry_after) + 1)
    return JSONResponse({"detail": detail, "request_id": rid}, status_code=status, headers=headers)


class Gateway:
    def __init__(self, app: ASGIApp, platform: Platform, origins: list[str]) -> None:
        self.app = app
        self.platform = platform
        self.origins = {o.rstrip("/") for o in origins if o}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        method: str = scope["method"]
        path: str = scope["path"]
        headers = Headers(scope=scope)
        rid_in = headers.get("x-request-id", "")
        rid = rid_in if _RID.match(rid_in) else secrets.token_hex(8)
        client = scope.get("client")
        ip = client[0] if client else ""
        state: dict[str, Any] = scope.setdefault("state", {})
        state.update(request_id=rid, platform=self.platform, ip=ip, principal=None)

        status = 0
        streaming = False
        hook: Hook | None = None
        captured: list[bytes] = []
        api = path.startswith("/api") or path.startswith("/docs") or path in ("/openapi.json", "/redoc")
        no_store = path.startswith("/api/v1")
        hsts = self.platform.settings.hsts

        async def send_wrapper(message: Message) -> None:
            nonlocal status, streaming
            if message["type"] == "http.response.start":
                status = int(message["status"])
                h = MutableHeaders(scope=message)
                h["x-request-id"] = rid
                for k, v in SECURITY_HEADERS:
                    if k.decode() not in h:
                        h[k.decode()] = v.decode()
                if hsts:
                    h["strict-transport-security"] = "max-age=31536000; includeSubDomains"
                if no_store and "cache-control" not in h:
                    h["cache-control"] = "no-store"
                streaming = h.get("content-type", "").startswith("text/event-stream")
            elif message["type"] == "http.response.body" and hook is not None and 200 <= status < 300:
                captured.append(message.get("body", b""))
                if not message.get("more_body", False):
                    body = b"".join(captured)
                    self.platform.spawn(hook(body))
            await send(message)

        limit = self._body_limit(path)
        length = headers.get("content-length")
        received = 0

        async def receive_limited() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _TooLarge
            return message

        principal: Principal | None = None
        try:
            if length is not None and length.isdigit() and int(length) > limit:
                await _deny(413, "The request is too large.", rid)(scope, receive, send_wrapper)
                return
            if api:
                principal = await self._authenticate(headers)
                state["principal"] = principal
                denial, hook, receive_next = await self._check(
                    scope, headers, method, path, ip, rid, principal, receive_limited
                )
                if denial is not None:
                    await denial(scope, receive, send_wrapper)
                    return
                await self.app(scope, receive_next, send_wrapper)
            else:
                await self.app(scope, receive, send_wrapper)
        except _TooLarge:
            if status == 0:
                await _deny(413, "The request is too large.", rid)(scope, receive, send_wrapper)
        except Exception:
            log.exception("unhandled error rid=%s %s %s", rid, method, path)
            if status == 0:
                await _deny(500, "Something went wrong on our side. Please try again.", rid)(
                    scope, receive, send_wrapper
                )
            else:
                raise
        finally:
            elapsed = time.perf_counter() - started
            final = status or 500
            self.platform.metrics.record(method, path, final, elapsed, streaming)
            if api:
                level = logging.WARNING if final >= 500 else logging.INFO
                if method in ("GET", "HEAD") and final < 400:
                    level = logging.DEBUG
                log.log(
                    level,
                    "%s %s %d %.0fms rid=%s user=%s",
                    method,
                    path,
                    final,
                    elapsed * 1000,
                    rid,
                    principal.user_id[:8] if principal else "-",
                )

    # ------------------------------------------------------------------ steps

    def _body_limit(self, path: str) -> int:
        s = self.platform.settings
        return s.max_upload_bytes if path == "/api/doubts/transcribe" else s.max_body_bytes

    async def _authenticate(self, headers: Headers) -> Principal | None:
        auth = headers.get("authorization", "")
        if auth[:7].lower() == "bearer ":
            return await self.platform.principal(auth[7:].strip(), via_cookie=False)
        token = cookie_parser(headers.get("cookie", "")).get(ACCESS_COOKIE)
        return await self.platform.principal(token, via_cookie=True) if token else None

    def _origin_ok(self, headers: Headers) -> bool:
        origin = headers.get("origin")
        if not origin or origin == "null":
            return origin is None
        origin = origin.rstrip("/")
        host = headers.get("host", "")
        if origin in self.origins or origin.split("://", 1)[-1] == host:
            return True
        # Development: the app on this machine under any local name or port (localhost, 127.0.0.1).
        if not self.platform.settings.production:
            name = origin.split("://", 1)[-1].rsplit(":", 1)[0]
            return name in ("localhost", "127.0.0.1", "[::1]")
        return False

    async def _check(
        self,
        scope: Scope,
        headers: Headers,
        method: str,
        path: str,
        ip: str,
        rid: str,
        p: Principal | None,
        receive: Receive,
    ) -> tuple[Response | None, Hook | None, Receive]:
        platform = self.platform
        if method in UNSAFE:
            if not self._origin_ok(headers):
                return _deny(403, "Cross-site request refused.", rid), None, receive
            if p is not None and p.via_cookie and not CSRF_EXEMPT.match(path):
                sent = headers.get(CSRF_HEADER, "")
                cookie = cookie_parser(headers.get("cookie", "")).get(CSRF_COOKIE, "")
                if not sent or not cookie or not secrets.compare_digest(sent, cookie):
                    return (
                        _deny(403, "Your session's security check failed. Reload the page.", rid),
                        None,
                        receive,
                    )

        if not path.startswith("/api/tts/"):
            key = f"u:{p.user_id}" if p else f"ip:{ip}"
            wait = platform.limiter.hit(f"all:{key}", platform.settings.rate_limit_per_min, 60)
            if wait is not None:
                return _deny(429, "Too many requests. Slow down a little.", rid, wait), None, receive

        if path.startswith("/api/v1/") or path == "/api/v1":
            return None, None, receive

        rule = next((r for r in LEGACY if method in r.methods and r.pattern.match(path)), None)
        if rule is None:
            # Anything else on the API (a route added later and not yet given a rule) is for admins.
            if p is None:
                return _deny(401, "Sign in to continue.", rid), None, receive
            if not p.can(Perm.DEBUG):
                return _deny(403, "You do not have access to this.", rid), None, receive
            return None, None, receive

        m = rule.pattern.match(path)
        assert m is not None
        groups = m.groupdict()
        access = rule.access
        if access == "public":
            return None, None, receive
        if access == "docs":
            if not platform.settings.docs:
                return _deny(404, "Not Found", rid), None, receive
            access = "perm"
        if p is None and access not in ("reader",):
            return _deny(401, "Sign in to continue.", rid), None, receive
        if access == "perm":
            assert p is not None and rule.perm is not None
            if not p.can(rule.perm):
                return _deny(403, "You do not have access to this.", rid), None, receive
        elif access in ("reader", "owner", "doubt_ask", "doubt_read"):
            gid = groups["gid"]
            lesson = await platform.lesson_access(gid)
            admin = p is not None and p.can(Perm.LESSON_ANY)
            readable = admin or (lesson is not None and lesson.readable_by(p))
            if not readable:
                if p is None:
                    return _deny(401, "Sign in to watch this lesson.", rid), None, receive
                return _deny(404, "No such lesson.", rid), None, receive
            if access == "owner" and not (admin or (lesson is not None and lesson.owned_by(p))):
                return _deny(403, "Only the lesson's owner can do this.", rid), None, receive
            if access == "doubt_read":
                assert p is not None
                if not admin and not await self._owns_doubt(p, groups["sid"]):
                    return _deny(404, "No such doubt.", rid), None, receive
            if access == "doubt_ask":
                assert p is not None
                denial, receive = await self._doubt_gate(p, rid, receive)
                if denial is not None:
                    return denial, None, receive
                return None, self._doubt_hook(p, gid), receive

        if rule.hook == "lesson" and p is not None:
            return None, self._lesson_hook(p), receive
        return None, None, receive

    # ------------------------------------------------------------------ doubts

    async def _owns_doubt(self, p: Principal, session_id: str) -> bool:
        async with self.platform.db.session() as s:
            hit = await s.scalar(
                select(DoubtLog.id)
                .where(DoubtLog.session_id == session_id, DoubtLog.user_id == p.user_id)
                .limit(1)
            )
        return hit is not None

    async def _doubt_gate(self, p: Principal, rid: str, receive: Receive) -> tuple[Response | None, Receive]:
        platform = self.platform
        if not p.can(Perm.DOUBT_ASK):
            return _deny(403, "Your account cannot ask doubts.", rid), receive
        wait = platform.limiter.hit(f"doubt:{p.user_id}", 30, 3600)
        if wait is not None:
            return _deny(429, "You have asked many doubts this hour. Take a short break.", rid, wait), receive
        quota = platform.settings.doubts_per_day(p.role)
        if quota > 0:
            async with platform.db.session() as s:
                asked = await s.scalar(
                    select(func.count(func.distinct(DoubtLog.id))).where(
                        DoubtLog.user_id == p.user_id, DoubtLog.created_at > time.time() - 86400
                    )
                )
            if (asked or 0) >= quota:
                return _deny(
                    429, f"You have used today's {quota} doubt answers. Come back tomorrow.", rid
                ), receive
        # A follow-up continues a doubt: only its own asker may continue it.
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        try:
            sid = json.loads(body or b"{}").get("session_id")
        except (ValueError, AttributeError):
            sid = None
        if isinstance(sid, str) and sid and not p.can(Perm.LESSON_ANY) and not await self._owns_doubt(p, sid):
            return _deny(404, "No such doubt.", rid), receive
        sent = False

        async def replay() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return None, replay

    def _doubt_hook(self, p: Principal, gid: str) -> Hook:
        async def record(body: bytes) -> None:
            data = json.loads(body)
            async with self.platform.db.session() as s:
                s.add(
                    DoubtLog(
                        user_id=p.user_id,
                        lesson_id=gid,
                        session_id=str(data.get("session_id", ""))[:16],
                        turn=int(data.get("n", 1) or 1),
                        question=str(data.get("question", ""))[:1000],
                        kind=str(data.get("kind", ""))[:16],
                        title=str(data.get("title", ""))[:200],
                        cost_usd=float(data.get("cost_usd", 0) or 0),
                    )
                )
                await s.commit()

        return record

    def _lesson_hook(self, p: Principal) -> Hook:
        """A lesson made through the pipeline's own API (admins): it is theirs in the catalog."""

        async def record(body: bytes) -> None:
            from animator.platform.catalog import subject_of

            data = json.loads(body)
            req = data.get("request", {})
            async with self.platform.db.session() as s:
                if await s.get(Lesson, data["id"]) is None:
                    s.add(
                        Lesson(
                            id=data["id"],
                            owner_id=p.user_id,
                            topic=str(req.get("topic", ""))[:500],
                            title=str(req.get("topic", ""))[:200],
                            audience=str(req.get("audience", ""))[:120],
                            language=str(req.get("language", "English"))[:40],
                            duration_min=float(req.get("duration_min", 3)),
                            status=str(data.get("status", "queued")),
                            subject=subject_of(str(req.get("topic", ""))),
                            created_at=float(data.get("created_at", time.time())),
                        )
                    )
                    await s.commit()
            self.platform.forget_lesson(data["id"])

        return record
