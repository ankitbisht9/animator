"""How a lesson may be shared (ADR-057): the rules every route that changes a lesson's sharing follows,
so the admin's lesson list, its bulk actions and a lesson's own share dialog always agree.

- Public (listed in Explore) needs a lesson that is ready and not removed.
- Featured needs a public lesson: featuring asks for public first, and a lesson that stops being public
  (private, link only, or removed) stops being featured with it.
- A removed lesson is private and not featured; restoring it keeps it private.

`apply` checks the whole change first and changes nothing when it refuses (`SharingError`, a 409 for
the routes), so a refusal never leaves a lesson half changed.
"""

from __future__ import annotations

import time

from animator.platform.models import Lesson

VISIBILITIES = ("private", "unlisted", "public")


class SharingError(Exception):
    """Why the change is not allowed, in words for the person who asked."""


def apply(
    row: Lesson,
    *,
    visibility: str | None = None,
    featured: bool | None = None,
    deleted: bool | None = None,
    now: float | None = None,
) -> bool:
    """Change the lesson's sharing as asked, by the rules above. True when anything changed."""
    if visibility is not None and visibility not in VISIBILITIES:
        raise SharingError(f"Unknown sharing: {visibility}.")
    removed = row.deleted_at is not None if deleted is None else deleted
    target_vis = row.visibility if visibility is None else visibility
    target_featured = row.featured if featured is None else featured
    if removed:
        if visibility not in (None, "private"):
            raise SharingError("This lesson was removed. Restore it first.")
        if featured:
            raise SharingError("This lesson was removed. Restore it first.")
        target_vis, target_featured = "private", False
    publishing = visibility == "public" and row.visibility != "public"
    if publishing and row.status != "complete":
        raise SharingError("A lesson can be published once it is ready.")
    if target_featured and target_vis != "public":
        if featured:
            raise SharingError("Only public lessons can be featured. Publish it first.")
        target_featured = False  # it stops being public: it stops being featured
    changed = False
    if removed != (row.deleted_at is not None):
        row.deleted_at = (time.time() if now is None else now) if removed else None
        changed = True
    if target_vis != row.visibility:
        row.visibility = target_vis
        changed = True
    if bool(target_featured) != bool(row.featured):
        row.featured = bool(target_featured)
        changed = True
    return changed
