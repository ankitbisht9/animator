"""FastAPI dependencies of /api/v1: the platform, a database session, and who is asking (resolved by the
gateway; these only read it and refuse when it does not fit)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from animator.platform.core import Platform, Principal
from animator.platform.rbac import Perm


def get_platform(request: Request) -> Platform:
    platform: Platform = request.app.state.platform
    return platform


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with get_platform(request).db.session() as s:
        yield s


def maybe_principal(request: Request) -> Principal | None:
    return getattr(request.state, "principal", None)


def signed_in(request: Request) -> Principal:
    p = maybe_principal(request)
    if p is None:
        raise HTTPException(401, "Sign in to continue.")
    return p


def require(perm: Perm) -> Callable[[Request], Principal]:
    def check(request: Request) -> Principal:
        p = signed_in(request)
        if not p.can(perm):
            raise HTTPException(403, "You do not have access to this.")
        return p

    return check


def client_ip(request: Request) -> str:
    return str(getattr(request.state, "ip", "") or (request.client.host if request.client else ""))


Plat = Annotated[Platform, Depends(get_platform)]
DB = Annotated[AsyncSession, Depends(get_db)]
Me = Annotated[Principal, Depends(signed_in)]
MaybeMe = Annotated[Principal | None, Depends(maybe_principal)]
Admin = Annotated[Principal, Depends(require(Perm.USER_MANAGE))]
Staff = Annotated[Principal, Depends(require(Perm.SUPPORT_MANAGE))]
Moderator = Annotated[Principal, Depends(require(Perm.LESSON_ANY))]
Auditor = Annotated[Principal, Depends(require(Perm.AUDIT_READ))]
Analyst = Annotated[Principal, Depends(require(Perm.ANALYTICS_READ))]
UsageReader = Annotated[Principal, Depends(require(Perm.USAGE_READ))]
CreditManager = Annotated[Principal, Depends(require(Perm.CREDITS_MANAGE))]
RatingsReader = Annotated[Principal, Depends(require(Perm.RATINGS_READ))]
Curator = Annotated[Principal, Depends(require(Perm.EXPLORE_CURATE))]
Reviewer = Annotated[Principal, Depends(require(Perm.REVIEW_MANAGE))]
ObjectsReader = Annotated[Principal, Depends(require(Perm.OBJECTS_READ))]
ObjectsCurator = Annotated[Principal, Depends(require(Perm.OBJECTS_CURATE))]
