"""The platform object: the database, the rate limiter, the mailer, request metrics, the caches that
keep per-request checks cheap, and the background work (the lesson catalog). One per API process."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Coroutine
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from animator.platform.db import Database
from animator.platform.mail import Mailer
from animator.platform.metrics import Metrics
from animator.platform.models import AuditEvent, AuthSession, Lesson, Notification, User
from animator.platform.ratelimit import RateLimiter
from animator.platform.rbac import Perm, has, perms_of
from animator.platform.security import read_access
from animator.platform.settings import PlatformSettings

if TYPE_CHECKING:
    from animator.config import Settings
    from animator.doubts import DoubtService
    from animator.jobs.runner import GenerationRunner

log = logging.getLogger("animator.platform")

PRINCIPAL_TTL_S = 10.0
LESSON_TTL_S = 5.0


@dataclass(frozen=True)
class Principal:
    """The signed-in person behind a request (resolved by the gateway)."""

    user_id: str
    session_id: str
    role: str
    email: str
    name: str
    verified: bool
    via_cookie: bool = True

    def can(self, perm: Perm) -> bool:
        return has(self.role, perm)

    @property
    def perms(self) -> list[str]:
        return sorted(p.value for p in perms_of(self.role))


@dataclass(frozen=True)
class LessonAccess:
    owner_id: str | None
    visibility: str
    deleted: bool

    def readable_by(self, p: Principal | None) -> bool:
        if p is not None and (
            p.can(Perm.LESSON_ANY) or (self.owner_id is not None and p.user_id == self.owner_id)
        ):
            return True
        return not self.deleted and self.visibility in ("public", "unlisted")

    def owned_by(self, p: Principal | None) -> bool:
        if p is None:
            return False
        if p.can(Perm.LESSON_ANY):
            return True
        return not self.deleted and self.owner_id is not None and p.user_id == self.owner_id


class Platform:
    def __init__(self, settings: PlatformSettings) -> None:
        self.settings = settings
        self.db = Database(settings.url, settings.db_echo)
        self.limiter = RateLimiter()
        self.mailer = Mailer(settings)
        self.metrics = Metrics()
        self.started_at = time.time()
        self._principals: dict[str, tuple[float, Principal | None]] = {}
        self._lessons: dict[str, tuple[float, LessonAccess | None]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._stop = asyncio.Event()
        # Set by start(): the pipeline's runner and settings, the doubts service, the engine pool.
        self.runner: GenerationRunner | None = None
        self.app_settings: Settings | None = None
        self.doubts: DoubtService | None = None
        self.engine: Any = None

    # ------------------------------------------------------------------ life

    async def start(
        self, runner: GenerationRunner, app_settings: Settings, doubts: DoubtService | None, engine: Any
    ) -> None:
        from animator.platform.catalog import Catalog

        self.runner = runner
        self.app_settings = app_settings
        self.doubts = doubts
        self.engine = engine
        await self.db.migrate()
        _ = self.settings.secret  # create the signing key now, not inside a request
        catalog = Catalog(self)
        self.catalog = catalog
        self.spawn(catalog.run(self._stop))
        log.info("platform ready (%s)", "SQLite" if self.db.sqlite else "database")

    async def close(self) -> None:
        self._stop.set()
        for t in list(self._tasks):
            t.cancel()
        for t in list(self._tasks):
            # Shutting down: each task logs its own errors.
            with contextlib.suppress(BaseException):
                await t
        await self.db.close()

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Run in the background; errors are logged, the task is awaited on shutdown."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)

        def done(t: asyncio.Task[Any]) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                log.error("background task failed", exc_info=t.exception())

        task.add_done_callback(done)
        return task

    # ------------------------------------------------------------------ who is asking

    async def principal(self, token: str, via_cookie: bool = True) -> Principal | None:
        claims = read_access(self.settings.secret, token)
        if claims is None:
            return None
        hit = self._principals.get(claims.session_id)
        now = time.monotonic()
        if hit is not None and hit[0] > now:
            p = hit[1]
        else:
            p = await self._load_principal(claims.session_id)
            self._principals[claims.session_id] = (now + PRINCIPAL_TTL_S, p)
            if len(self._principals) > 20_000:
                self._principals = {k: v for k, v in self._principals.items() if v[0] > now}
        if p is None or p.user_id != claims.user_id:
            return None
        return p if p.via_cookie == via_cookie else replace(p, via_cookie=via_cookie)

    async def _load_principal(self, session_id: str) -> Principal | None:
        async with self.db.session() as s:
            row = (
                await s.execute(
                    select(User.id, User.role, User.status, User.email, User.name, User.email_verified_at)
                    .join(AuthSession, AuthSession.user_id == User.id)
                    .where(
                        AuthSession.id == session_id,
                        AuthSession.revoked_at.is_(None),
                        AuthSession.expires_at > time.time(),
                    )
                )
            ).first()
        if row is None or row.status != "active":
            return None
        return Principal(row.id, session_id, row.role, row.email, row.name, row.email_verified_at is not None)

    def forget(self, user_id: str | None = None, session_id: str | None = None) -> None:
        """Drop cached principals after a sign-out, a role change or a suspension."""
        if session_id is not None:
            self._principals.pop(session_id, None)
        if user_id is not None:
            self._principals = {
                k: v for k, v in self._principals.items() if v[1] is None or v[1].user_id != user_id
            }

    # ------------------------------------------------------------------ lessons

    async def lesson_access(self, lesson_id: str) -> LessonAccess | None:
        hit = self._lessons.get(lesson_id)
        now = time.monotonic()
        if hit is not None and hit[0] > now:
            return hit[1]
        async with self.db.session() as s:
            row = (
                await s.execute(
                    select(Lesson.owner_id, Lesson.visibility, Lesson.deleted_at).where(
                        Lesson.id == lesson_id
                    )
                )
            ).first()
        access = LessonAccess(row.owner_id, row.visibility, row.deleted_at is not None) if row else None
        self._lessons[lesson_id] = (now + LESSON_TTL_S, access)
        if len(self._lessons) > 20_000:
            self._lessons = {k: v for k, v in self._lessons.items() if v[0] > now}
        return access

    def forget_lesson(self, lesson_id: str) -> None:
        self._lessons.pop(lesson_id, None)

    # ------------------------------------------------------------------ records

    async def audit(
        self, action: str, actor_id: str | None, target: str = "", ip: str = "", **detail: Any
    ) -> None:
        async with self.db.session() as s:
            s.add(AuditEvent(actor_id=actor_id, action=action, target=target, ip=ip, detail=detail))
            await s.commit()

    async def notify(self, user_id: str, kind: str, title: str, body: str = "", link: str = "") -> None:
        async with self.db.session() as s:
            s.add(Notification(user_id=user_id, kind=kind, title=title[:200], body=body[:500], link=link))
            await s.commit()
