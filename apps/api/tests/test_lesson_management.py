"""Lesson management for admins (ADR-057): the sharing rules (featured lessons are public ones), the
admin's lesson list with its counts, filters and order, actions on many lessons at once, and Explore's
Featured shelf, which holds only lessons an admin featured."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update

from animator.jobs.store import MemoryStore
from animator.pipeline.models import Generation, GenerationRequest
from animator.platform import catalog as catalog_module
from animator.platform import sharing
from animator.platform.models import Lesson
from tests.test_platform import admin_client, csrf, make_app, register


@pytest.fixture(autouse=True)
def quick_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog_module, "SYNC_EVERY_S", 0.1)


# ---------------------------------------------------------------------- the rules


def lesson(**kw: Any) -> Lesson:
    base: dict[str, Any] = {
        "status": "complete",
        "visibility": "private",
        "featured": False,
        "deleted_at": None,
    }
    base.update(kw)
    return cast(Lesson, SimpleNamespace(**base))


def test_featured_lessons_are_public_ones() -> None:
    row = lesson()
    with pytest.raises(sharing.SharingError, match="Publish it first"):
        sharing.apply(row, featured=True)
    assert row.featured is False and row.visibility == "private"  # a refusal changes nothing

    assert sharing.apply(row, visibility="public", featured=True)  # both at once
    assert (row.visibility, row.featured) == ("public", True)
    assert not sharing.apply(row, featured=True)  # nothing to change

    assert sharing.apply(row, visibility="unlisted")
    assert (row.visibility, row.featured) == ("unlisted", False)  # not public: not featured


def test_publishing_needs_a_ready_lesson_and_removing_unshares() -> None:
    making = lesson(status="generating")
    with pytest.raises(sharing.SharingError, match="once it is ready"):
        sharing.apply(making, visibility="public")
    assert making.visibility == "private"

    row = lesson(visibility="public", featured=True)
    assert sharing.apply(row, deleted=True, now=123.0)
    assert (row.deleted_at, row.visibility, row.featured) == (123.0, "private", False)
    for change in ({"visibility": "public"}, {"visibility": "unlisted"}, {"featured": True}):
        with pytest.raises(sharing.SharingError, match="Restore it first"):
            sharing.apply(row, **change)
    assert not sharing.apply(row, visibility="private")  # already
    assert sharing.apply(row, deleted=False)
    assert (row.deleted_at, row.visibility) == (None, "private")  # restored, still private
    with pytest.raises(sharing.SharingError, match="Unknown sharing"):
        sharing.apply(row, visibility="everyone")


# ---------------------------------------------------------------------- the admin's lesson list


TOPICS = [
    ("How does photosynthesis work?", "complete"),
    ("Why do we have seasons?", "complete"),
    ("Explain Archimedes' principle", "complete"),
    ("How does binary search work?", "complete"),
    ("What is recursion?", "paused"),
]


def seeded_app(tmp: Path) -> tuple[TestClient, list[str]]:
    """An app whose store holds lessons made before accounts: they go to the first admin."""
    store = MemoryStore()
    ids: list[str] = []
    for topic, status in TOPICS:
        g = Generation(request=GenerationRequest(topic=topic))
        g.status = status  # type: ignore[assignment]
        asyncio.run(store.save(g))
        ids.append(g.id)
    return make_app(tmp, store=store), ids


def wait_for_lessons(admin: TestClient, n: int) -> None:
    deadline = time.time() + 10
    while time.time() < deadline:
        if admin.get("/api/v1/admin/lessons").json()["total"] >= n:
            return
        time.sleep(0.1)
    raise AssertionError("the lessons never reached the catalog")


def test_admins_manage_sharing_and_featuring_from_the_list(tmp_path: Path, worker_script: Path) -> None:
    app, ids = seeded_app(tmp_path)
    photo, seasons, archimedes, search, recursion = ids
    with app as c:
        admin = admin_client(c)
        wait_for_lessons(admin, len(ids))
        h = csrf(admin)

        got = admin.get("/api/v1/admin/lessons").json()
        assert got["counts"] == {
            "all": 5,
            "public": 0,
            "featured": 0,
            "unlisted": 0,
            "private": 5,
            "deleted": 0,
        }
        assert admin.get("/api/v1/explore/featured").json() == []

        # One at a time: featuring asks for public first.
        r = admin.patch(f"/api/v1/admin/lessons/{photo}", json={"featured": True}, headers=h)
        assert r.status_code == 409 and "Publish it first" in r.json()["detail"]
        r = admin.patch(
            f"/api/v1/admin/lessons/{photo}", json={"visibility": "public", "featured": True}, headers=h
        )
        assert r.status_code == 200 and (r.json()["visibility"], r.json()["featured"]) == ("public", True)
        r = admin.patch(f"/api/v1/admin/lessons/{recursion}", json={"visibility": "public"}, headers=h)
        assert r.status_code == 409 and "once it is ready" in r.json()["detail"]

        # Many at once: each lesson by the same rules.
        r = admin.post(
            "/api/v1/admin/lessons/bulk",
            json={"ids": [seasons, archimedes, recursion, "g_nosuchlesson", seasons], "action": "publish"},
            headers=h,
        )
        assert r.status_code == 200
        out = r.json()
        assert out["changed"] == [seasons, archimedes] and out["unchanged"] == []
        assert {x["id"]: x["reason"] for x in out["skipped"]} == {
            recursion: "A lesson can be published once it is ready.",
            "g_nosuchlesson": "No such lesson.",
        }
        r = admin.post(
            "/api/v1/admin/lessons/bulk",
            json={"ids": [seasons, search, photo], "action": "feature"},
            headers=h,
        )
        out = r.json()
        assert out["changed"] == [seasons] and out["unchanged"] == [photo]
        assert [x["id"] for x in out["skipped"]] == [search]

        counts = admin.get("/api/v1/admin/lessons").json()["counts"]
        assert counts == {"all": 5, "public": 3, "featured": 2, "unlisted": 0, "private": 2, "deleted": 0}
        featured = admin.get("/api/v1/admin/lessons", params={"featured": True}).json()
        assert {x["id"] for x in featured["items"]} == {photo, seasons} and featured["total"] == 2

        # Explore's Featured shelf: only what was featured; All lessons: every public lesson.
        assert {x["id"] for x in c.get("/api/v1/explore/featured").json()} == {photo, seasons}
        assert {x["id"] for x in c.get("/api/v1/explore").json()["items"]} == {photo, seasons, archimedes}

        # Making it link-only or private ends featuring; so does removing; restoring keeps it private.
        out = admin.post(
            "/api/v1/admin/lessons/bulk", json={"ids": [seasons, archimedes], "action": "unlist"}, headers=h
        ).json()
        assert out["changed"] == [seasons, archimedes]
        assert {x["id"] for x in c.get("/api/v1/explore/featured").json()} == {photo}
        out = admin.post(
            "/api/v1/admin/lessons/bulk", json={"ids": [photo], "action": "delete"}, headers=h
        ).json()
        assert out["changed"] == [photo]
        assert c.get("/api/v1/explore/featured").json() == []
        deleted = admin.get("/api/v1/admin/lessons", params={"deleted": True}).json()
        assert [x["id"] for x in deleted["items"]] == [photo] and deleted["items"][0]["featured"] is False
        out = admin.post(
            "/api/v1/admin/lessons/bulk", json={"ids": [photo], "action": "restore"}, headers=h
        ).json()
        assert out["changed"] == [photo]
        row = admin.get(f"/api/v1/lessons/{photo}").json()
        assert (row["visibility"], row["featured"], row["deleted"]) == ("private", False, False)

        # The list: words (title, owner, id), statuses, subjects, order.
        assert admin.get("/api/v1/admin/lessons", params={"q": "binary"}).json()["total"] == 1
        assert admin.get("/api/v1/admin/lessons", params={"q": "head teacher"}).json()["total"] == 5
        assert admin.get("/api/v1/admin/lessons", params={"q": search[2:9]}).json()["total"] == 1
        stopped = admin.get("/api/v1/admin/lessons", params={"status": "paused,failed,cancelled"}).json()
        assert [x["id"] for x in stopped["items"]] == [recursion]
        assert stopped["counts"]["all"] == 1  # the counts follow the words and filters
        titles = [
            x["title"] for x in admin.get("/api/v1/admin/lessons", params={"sort": "title"}).json()["items"]
        ]
        assert titles == sorted(titles, key=str.lower)
        assert admin.get("/api/v1/admin/lessons", params={"subject": "biology"}).json()["total"] >= 1

        # Bad requests, and people who may not.
        assert (
            admin.post(
                "/api/v1/admin/lessons/bulk", json={"ids": [], "action": "publish"}, headers=h
            ).status_code
            == 422
        )
        bad = admin.post("/api/v1/admin/lessons/bulk", json={"ids": [photo], "action": "explode"}, headers=h)
        assert bad.status_code == 422
        register(c, "asha@example.org")
        assert (
            c.post(
                "/api/v1/admin/lessons/bulk", json={"ids": [photo], "action": "publish"}, headers=csrf(c)
            ).status_code
            == 403
        )
        audit = admin.get("/api/v1/admin/audit", params={"action": "lesson."}).json()["items"]
        assert any(e["detail"].get("bulk") for e in audit)


def test_an_owner_making_a_featured_lesson_link_only_unfeatures_it(
    tmp_path: Path, worker_script: Path
) -> None:
    app, ids = seeded_app(tmp_path)
    photo = ids[0]
    with app as c:
        admin = admin_client(c)
        wait_for_lessons(admin, len(ids))
        h = csrf(admin)
        assert admin.patch(
            f"/api/v1/admin/lessons/{photo}", json={"visibility": "public", "featured": True}, headers=h
        ).json()["featured"]
        # The admin owns these lessons: the lesson's own share dialog (PATCH /lessons/{id}).
        r = admin.patch(f"/api/v1/lessons/{photo}", json={"visibility": "unlisted"}, headers=h)
        assert r.status_code == 200 and (r.json()["visibility"], r.json()["featured"]) == ("unlisted", False)
        r = admin.patch(f"/api/v1/lessons/{photo}", json={"featured": True}, headers=h)
        assert r.status_code == 409


def test_lessons_featured_before_the_rule_stop_being_featured(tmp_path: Path, worker_script: Path) -> None:
    app, ids = seeded_app(tmp_path)
    with app as c:
        admin = admin_client(c)
        wait_for_lessons(admin, len(ids))
        platform = cast(FastAPI, c.app).state.platform

        async def featured_while_private() -> int:
            async with platform.db.session() as s:
                await s.execute(update(Lesson).where(Lesson.id == ids[1]).values(featured=True))
                await s.commit()
            return int(await platform.catalog.unfeature_unshared())

        assert c.portal.call(featured_while_private) == 1  # type: ignore[union-attr]
        row = admin.get(f"/api/v1/lessons/{ids[1]}").json()
        assert row["featured"] is False
