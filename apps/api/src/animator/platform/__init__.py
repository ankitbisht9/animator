"""The platform (ADR-051): accounts, roles and permissions, the API gateway, the lesson catalog,
learning records, support and administration, around the lesson pipeline, which it never changes.

`install()` adds the gateway and the /api/v1 routes to the app; `Platform.start()` (in the app's
lifespan) migrates the database and starts the catalog. With `PLATFORM_ENABLED=false`, or when the app
is built with its own settings and no platform (the pipeline's tests), none of this exists and the API
is exactly as before.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, Request
from starlette.middleware.trustedhost import TrustedHostMiddleware

from animator.platform.core import Platform, Principal
from animator.platform.settings import PlatformSettings

public = APIRouter(prefix="/api/v1/public", tags=["public"])


@public.get("/info")
async def info(request: Request) -> dict[str, Any]:
    """What the sign-in and landing pages need to know (nothing private)."""
    from animator.platform.catalog import SUBJECTS

    platform: Platform = request.app.state.platform
    st = platform.app_settings
    ps = platform.settings
    invited = bool(ps.admin_list) or bool(ps.invite_emails.strip())
    return {
        # The sign-up form is shown when anyone, or someone invited, may sign up.
        "signup": ps.allow_signup or invited,
        "invite_only": not ps.allow_signup,
        "mail": platform.mailer.delivers,
        "doubts": platform.doubts is not None,
        "voice": bool(st and st.animator_tts_enabled),
        # Cost-saver lessons (ADR-055) are offered when saved drawings are on.
        "costsaver": bool(st and st.animator_objects_enabled),
        "languages": ["English", "Hinglish"],
        "subjects": [{"key": k, "label": v} for k, v in SUBJECTS.items()],
        "password_min_length": platform.settings.password_min_length,
    }


def install(app: FastAPI, platform: Platform, origins: list[str]) -> None:
    """Gateway and routes. Call before the CORS middleware is added, so CORS stays outermost."""
    from animator.platform.api import (
        account,
        admin,
        auth,
        credits,
        learning,
        lessons,
        objects,
        ratings,
        support,
    )
    from animator.platform.gateway import Gateway

    app.state.platform = platform
    app.add_middleware(Gateway, platform=platform, origins=[*origins, platform.settings.public_base_url])
    hosts = [h.strip() for h in platform.settings.trusted_hosts.split(",") if h.strip()]
    if hosts and hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    for module in (auth, account, lessons, learning, support, admin, credits, ratings, objects):
        app.include_router(module.router)
    app.include_router(public)


def serve_web(app: FastAPI, platform: Platform) -> None:
    """After every other route: the built web app on all remaining paths (production)."""
    if platform.settings.serve_web:
        from animator.export.site import WEB_DIST
        from animator.platform.web import serve

        serve(app, WEB_DIST)


__all__ = ["Platform", "PlatformSettings", "Principal", "install", "serve_web"]
