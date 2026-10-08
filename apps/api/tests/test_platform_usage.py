"""Credits and cost, ratings and Explore (ADR-053): the ledger (exact sums, posted once, across restarts),
the monthly credits and their enforcement on everything that spends, the reports, the Explore rule and
an admin's hand over it, the ratings monitor, and the migration of an existing database."""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Connection, select, text

from animator.api.app import create_app
from animator.jobs.store import MemoryStore
from animator.llm import FakeLLM
from animator.pipeline.models import Generation, GenerationRequest
from animator.platform import PlatformSettings, usage
from animator.platform import catalog as catalog_module
from animator.platform.api import credits as credits_api
from animator.platform.curation import Decision, decide, qualifies
from animator.platform.db import Database, alembic_config
from animator.platform.models import Lesson, UsageEntry, User
from tests.test_doubts import BOARD, Tutor
from tests.test_generations_api import read_events
from tests.test_pipeline import Script, settings
from tests.test_platform import (
    PASSWORD,
    admin_client,
    csrf,
    forget_lesson,
    other,
    platform_settings,
    register,
    wait_for,
)


@pytest.fixture(autouse=True)
def quick_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog_module, "SYNC_EVERY_S", 0.1)


def make_app(
    tmp: Path, llm: Any = None, store: MemoryStore | None = None, cost: float = 0.0, **kw: Any
) -> TestClient:
    """The platform over the real pipeline; every model call costs `cost` dollars."""
    s = settings(openai_api_key="sk-proj-secret-value-that-must-never-leak")
    app = create_app(
        settings=s,
        llm=FakeLLM(llm or Script(), cost=cost),
        store=store or MemoryStore(),
        resume_on_start=False,
        platform=platform_settings(tmp, **kw),
    )
    return TestClient(app)


def until(check: Any, timeout: float = 10) -> Any:
    end = time.time() + timeout
    while time.time() < end:
        got = check()
        if got:
            return got
        time.sleep(0.05)
    raise AssertionError("never happened")


def explore_ids(c: TestClient, **params: Any) -> list[str]:
    return [x["id"] for x in c.get("/api/v1/explore", params=params).json()["items"]]


# ---------------------------------------------------------------------- units


def test_the_explore_rule_is_exact() -> None:
    ps = PlatformSettings(_env_file=None)
    assert not qualifies(100, 20, ps)  # 20 people, all 5 stars: not more than 20
    assert qualifies(84, 21, ps)  # 21 people averaging exactly 4.0
    assert not qualifies(83, 21, ps)  # 3.95
    assert not qualifies(0, 0, ps)
    strict = PlatformSettings(_env_file=None, explore_min_average=4.25, explore_min_raters=4)
    assert qualifies(17, 4, strict) and not qualifies(16, 4, strict)


def test_what_decides_a_place_in_explore() -> None:
    ps = PlatformSettings(_env_file=None)
    base: dict[str, Any] = {
        "status": "complete",
        "deleted": False,
        "visibility": "unlisted",
        "opt_out": False,
        "rating_sum": 105,
        "rating_count": 21,
        "pin": "",
        "owner_active": True,
        "settings": ps,
    }

    def why(**kw: Any) -> str:
        return decide(**{**base, **kw}).reason

    assert decide(**base) == Decision(True, "auto", "rated")
    assert why(opt_out=True) == "opted_out"
    assert why(rating_count=20, rating_sum=100) == "below_rule"
    assert why(visibility="private", pin="include") == "private"  # private is private
    assert why(visibility="public", pin="exclude") == "excluded"  # an admin's no beats publishing
    assert decide(
        **{**base, "pin": "include", "owner_active": False, "rating_count": 0, "rating_sum": 0}
    ) == (Decision(True, "admin", "included"))
    assert why(visibility="public", owner_active=False) == "owner_suspended"
    assert decide(**{**base, "visibility": "public", "rating_count": 0, "rating_sum": 0}) == (
        Decision(True, "owner", "published")
    )
    assert why(status="generating", pin="include") == "not_ready"
    assert why(deleted=True, pin="include") == "removed"
    assert why(settings=PlatformSettings(_env_file=None, explore_auto=False)) == "auto_off"


def test_periods_follow_the_platform_clock() -> None:
    ps = PlatformSettings(_env_file=None, timezone="Asia/Kolkata")
    ist = ZoneInfo("Asia/Kolkata")
    # Wednesday 7 October 20:00 UTC is Thursday 8 October 01:30 in India.
    ck = usage.clock(ps, dt.datetime(2026, 10, 7, 20, 0, tzinfo=dt.UTC).timestamp())
    assert ck.today == dt.date(2026, 10, 8)
    assert ck.since("today") == dt.datetime(2026, 10, 8, tzinfo=ist).timestamp()
    assert ck.since("week") == dt.datetime(2026, 10, 5, tzinfo=ist).timestamp()  # Monday
    assert ck.since("month") == dt.datetime(2026, 10, 1, tzinfo=ist).timestamp()
    assert ck.since("total") is None
    december = usage.clock(ps, dt.datetime(2026, 12, 31, 12, tzinfo=dt.UTC).timestamp())
    assert december.next_month == dt.datetime(2027, 1, 1, tzinfo=ist).timestamp()
    assert PlatformSettings(_env_file=None, timezone="Mars/Olympus").zone is None  # this computer's clock
    assert PlatformSettings(_env_file=None).zone is None


def test_money_and_credits_are_exact() -> None:
    ps = PlatformSettings(_env_file=None)
    assert usage.to_micros(0.118361) == 118_361
    assert usage.milli_of(10_000, ps) == 1000  # $0.01 is one credit
    assert usage.milli_of(118_361, ps) == 11_836
    assert usage.show_credits(1_000_000) == "1,000" and usage.show_credits(12_500) == "12.5"
    assert usage.limit_milli(ps, "learner", "role", 0) == 1_000_000
    assert usage.limit_milli(ps, "admin", "role", 0) is None  # 0 is no limit
    assert usage.limit_milli(ps, "learner", "custom", 50) == 50_000
    assert usage.limit_milli(ps, "learner", "blocked", 0) == 0
    assert usage.limit_milli(ps, "educator", "unlimited", 0) is None
    assert ps.voice_price("ElevenLabs") == 200.0 and ps.voice_price("edge") == 0.0
    assert PlatformSettings(_env_file=None, voice_prices={"elevenlabs": 165}).voice_price("elevenlabs") == 165
    assert PlatformSettings(_env_file=None, voice_prices="").voice_prices == {}  # as .env.example leaves it
    assert PlatformSettings(_env_file=None, voice_prices='{"openai": 12}').voice_price("openai") == 12
    assert usage.doubt_voice({"narration": None, "sentences": ["Hi."]}) == (0, "")
    assert usage.doubt_voice(
        {"narration": {"provider": "elevenlabs", "beats": [{}]}, "sentences": ["Hi there.", "Bye."]}
    ) == (14, "elevenlabs")


def test_csv_cells_cannot_become_formulas() -> None:
    assert credits_api._cell('=HYPERLINK("x")') == '\'=HYPERLINK("x")'
    assert credits_api._cell("+1") == "'+1" and credits_api._cell("@SUM") == "'@SUM"
    assert credits_api._cell("Asha Rao") == "Asha Rao" and credits_api._cell(None) == ""


# ---------------------------------------------------------------------- the ledger


def test_spend_is_posted_once_and_reported(tmp_path: Path, worker_script: Path) -> None:
    store = MemoryStore()
    old = Generation(request=GenerationRequest(topic="How does the heart pump blood?"))
    old.status = "complete"
    old.metrics.cost_usd = 0.118361
    old.metrics.tts_chars = 1234
    asyncio.run(store.save(old))
    voice = {"edge": 100}  # USD per million characters: 1,234 characters cost $0.1234
    with make_app(tmp_path, store=store, voice_prices=voice) as c:
        admin = admin_client(c)  # the first admin: the lesson made before accounts is theirs, spend included

        def total() -> dict[str, Any] | None:
            t: dict[str, Any] = admin.get("/api/v1/admin/usage/summary").json()["periods"]["total"]
            return t if t["entries"] >= 2 else None

        t = until(total)
        assert t["by_kind"]["lesson_ai"]["cost_usd"] == 0.118361
        assert t["by_kind"]["lesson_voice"]["cost_usd"] == 0.1234 and t["estimated_usd"] == 0.1234
        assert t["cost_usd"] == 0.241761 and t["credits"] == 24.176
        assert t["by_area"]["create"]["cost_usd"] == 0.241761 and t["by_area"]["learn"]["cost_usd"] == 0
        people = admin.get("/api/v1/admin/usage/people", params={"period": "total", "active": True}).json()
        assert people["total"] == 1 and people["items"][0]["spent"]["cost_usd"] == 0.241761
        lessons = admin.get("/api/v1/admin/usage/lessons", params={"period": "total"}).json()
        assert [x["id"] for x in lessons["items"]] == [old.id] and lessons["items"][0][
            "create_usd"
        ] == 0.241761
        sheet = admin.get("/api/v1/admin/usage/export.csv", params={"period": "total"})
        assert sheet.status_code == 200 and sheet.headers["content-type"].startswith("text/csv")
        lines = sheet.text.lstrip("﻿").strip().splitlines()
        assert lines[0].startswith("time,day,kind,area,person") and len(lines) == 3
        assert all(old.id in line for line in lines[1:])
        overview = admin.get("/api/v1/admin/overview").json()
        assert overview["ai_cost_usd"] == 0.2418
    # Started again on the same records: nothing is posted twice.
    with make_app(tmp_path, store=store, voice_prices=voice) as c:
        admin = admin_client(c)
        time.sleep(0.5)  # the catalog's start-up work
        t = admin.get("/api/v1/admin/usage/summary").json()["periods"]["total"]
        assert t["entries"] == 2 and t["cost_usd"] == 0.241761


def test_credits_are_enforced_and_can_be_changed(tmp_path: Path, worker_script: Path) -> None:
    tutor = Tutor()
    with make_app(tmp_path, llm=tutor, cost=0.002) as c:
        admin = admin_client(c)
        register(c, "asha@example.org")
        me = c.get("/api/v1/auth/me").json()["user"]
        assert me["credits"]["limit"] == 1000 and me["credits"]["used"] == 0
        assert me["credits"]["mode"] == "role" and not me["credits"]["exhausted"]
        lid = c.post("/api/v1/lessons", json={"topic": "Archimedes' principle"}, headers=csrf(c)).json()["id"]
        read_events(c, lid)
        wait_for(c, lid)

        # What making the lesson cost is hers, in credits ($0.01 each).
        def made() -> dict[str, Any] | None:
            got: dict[str, Any] = c.get("/api/v1/me/credits").json()
            return got if got["by_area"]["create"] > 0 else None

        mine = until(made)
        lesson_cost = c.get(f"/api/v1/lessons/{lid}").json()["cost_usd"]
        assert mine["by_area"]["create"] == pytest.approx(lesson_cost / 0.01, abs=0.002)
        assert mine["items"][0]["lesson_id"] == lid and mine["items"][0]["area"] == "create"

        # A doubt's answer: charged to whoever asked it.
        tutor.doubts = [BOARD]
        ask = {"question": "What is density?", "scene_index": 0, "t": 1.0}
        assert c.post(f"/api/doubts/{lid}", json=ask, headers=csrf(c)).status_code == 200
        until(lambda: c.get("/api/v1/me/credits").json()["by_area"]["learn"] > 0)

        # Her own allowance (1 credit a month), overspent by a charge: anything that spends is refused.
        uid = me["id"]
        people = f"/api/v1/admin/usage/people/{uid}"
        r = admin.put(f"{people}/credits", json={"mode": "custom", "limit": 1}, headers=csrf(admin))
        assert r.status_code == 200 and r.json()["limit"] == 1
        charge = admin.post(
            f"{people}/adjustments", json={"credits": -5, "note": "=Test charge"}, headers=csrf(admin)
        )
        assert charge.status_code == 201 and charge.json()["exhausted"]
        over = c.post("/api/v1/lessons", json={"topic": "Pendulums"}, headers=csrf(c))
        assert over.status_code == 402 and "renew" in over.json()["detail"]
        assert c.post(f"/api/doubts/{lid}", json=ask, headers=csrf(c)).status_code == 402
        speech = {**csrf(c), "content-type": "audio/webm"}
        assert c.post("/api/doubts/transcribe", content=b"\x00" * 64, headers=speech).status_code == 402
        assert c.get("/api/v1/auth/me").json()["user"]["credits"]["exhausted"] is True

        # Credits given back: she may spend again; she is told, with the reason.
        back = admin.post(
            f"{people}/adjustments", json={"credits": 10, "note": "Welcome back"}, headers=csrf(admin)
        )
        assert back.status_code == 201 and not back.json()["exhausted"]
        notes = c.get("/api/v1/notifications").json()["items"]
        assert [n["kind"] for n in notes].count("credits") >= 3
        assert any(
            a["note"] == "Welcome back" and a["credits"] == 10
            for a in c.get("/api/v1/me/credits").json()["adjustments"]
        )

        # Blocked: nothing that costs, whatever is left.
        admin.put(f"{people}/credits", json={"mode": "blocked"}, headers=csrf(admin))
        blocked = c.post("/api/v1/lessons", json={"topic": "Pendulums"}, headers=csrf(c))
        assert blocked.status_code == 402 and "cannot use credits" in blocked.json()["detail"]
        assert admin.put(f"{people}/credits", json={"mode": "custom"}, headers=csrf(admin)).status_code == 422
        zero = admin.post(
            f"{people}/adjustments", json={"credits": 0, "note": "nothing"}, headers=csrf(admin)
        )
        assert zero.status_code == 422
        admin.put(f"{people}/credits", json={"mode": "role"}, headers=csrf(admin))

        # Only admins see or change anyone's credits.
        assert c.get("/api/v1/admin/usage/summary").status_code == 403
        assert c.put(f"{people}/credits", json={"mode": "unlimited"}, headers=csrf(c)).status_code == 403

        # The reports.
        summary = admin.get("/api/v1/admin/usage/summary").json()
        today = summary["periods"]["today"]
        assert today["by_area"]["create"]["cost_usd"] > 0 and today["by_area"]["learn"]["cost_usd"] > 0
        assert (
            today["by_kind"]["adjustment"]["count"] == 2 and today["by_kind"]["adjustment"]["cost_usd"] == 0
        )
        assert len(summary["daily"]) == 30 and summary["daily"][-1]["cost_usd"] == today["cost_usd"]
        assert summary["people"]["spending_this_month"] == 1 and summary["averages"]["lesson_usd"] > 0
        person = admin.get(people).json()
        assert person["user"]["email"] == "asha@example.org" and person["lessons"][0]["id"] == lid
        assert {"=Test charge", "Welcome back"} <= {
            e["note"] for e in person["recent"] if e["kind"] == "adjustment"
        }
        assert all(e["by"] == "Head Teacher" for e in person["recent"] if e["kind"] == "adjustment")
        sheet = admin.get("/api/v1/admin/usage/export.csv", params={"period": "today"}).text
        assert "'=Test charge" in sheet and ",=Test charge" not in sheet  # never a formula
        actions = {e["action"] for e in admin.get("/api/v1/admin/audit").json()["items"]}
        assert {"credits.set", "credits.adjust", "usage.export"} <= actions


# ---------------------------------------------------------------------- ratings and Explore


def test_explore_by_ratings_and_by_hand(tmp_path: Path, worker_script: Path) -> None:
    with make_app(tmp_path, explore_min_raters=2) as c:
        admin = admin_client(c)
        register(c, "asha@example.org")
        lid = c.post("/api/v1/lessons", json={"topic": "Archimedes' principle"}, headers=csrf(c)).json()["id"]
        read_events(c, lid)
        wait_for(c, lid)
        lesson = f"/api/v1/lessons/{lid}"
        # Her own lesson: she cannot rate it.
        assert c.put(f"{lesson}/rating", json={"stars": 5}, headers=csrf(c)).status_code == 403
        mine = c.get(lesson).json()
        assert mine["can_rate"] is False and mine["in_explore"] is False
        assert mine["explore"]["rule"] == {"auto": True, "min_raters": 2, "min_average": 4.0}
        c.patch(lesson, json={"visibility": "unlisted"}, headers=csrf(c))
        forget_lesson(c, lid)
        raters: list[TestClient] = []
        for i in range(2):
            rater = other(c)
            register(rater, f"rater{i}@example.org", f"Rater {i}")
            raters.append(rater)
        assert raters[0].get(lesson).json()["can_rate"] is True
        assert (
            raters[0].put(f"{lesson}/rating", json={"stars": 5}, headers=csrf(raters[0])).status_code == 200
        )
        assert explore_ids(c) == []  # one person is not enough
        r = raters[1].put(f"{lesson}/rating", json={"stars": 3, "comment": "Fast"}, headers=csrf(raters[1]))
        assert r.json()["rating"] == 4.0 and r.json()["rating_count"] == 2
        assert explore_ids(c, subject="physics") == [lid]  # 4.0 from 2 people: in Explore by itself
        assert c.get(lesson).json()["explore"]["source"] == "auto"
        physics = next(x for x in c.get("/api/v1/explore/subjects").json() if x["key"] == "physics")
        assert physics["count"] == 1
        assert any(n["kind"] == "explore" for n in c.get("/api/v1/notifications").json()["items"])

        # The owner opts out, and back in.
        c.patch(lesson, json={"explore_opt_out": True}, headers=csrf(c))
        assert explore_ids(c) == []
        assert (
            raters[0].patch(lesson, json={"explore_opt_out": False}, headers=csrf(raters[0])).status_code
            == 403
        )
        c.patch(lesson, json={"explore_opt_out": False}, headers=csrf(c))
        assert explore_ids(c) == [lid]
        # A rating changed below the bar takes it out; back up, back in.
        raters[1].put(f"{lesson}/rating", json={"stars": 2}, headers=csrf(raters[1]))
        assert explore_ids(c) == []
        raters[1].put(f"{lesson}/rating", json={"stars": 3}, headers=csrf(raters[1]))
        assert explore_ids(c) == [lid]
        # A suspended rater's rating stops counting (and counts again when they are back).
        rater1 = raters[1].get("/api/v1/auth/me").json()["user"]["id"]
        admin.patch(f"/api/v1/admin/users/{rater1}", json={"status": "suspended"}, headers=csrf(admin))
        assert explore_ids(c) == [] and c.get(lesson).json()["rating_count"] == 1
        admin.patch(f"/api/v1/admin/users/{rater1}", json={"status": "active"}, headers=csrf(admin))
        assert explore_ids(c) == [lid] and c.get(lesson).json()["rating_count"] == 2

        # An admin keeps it out whatever its ratings say, then hands it back to the rule.
        curate = f"/api/v1/admin/explore/{lid}"
        out = admin.put(
            curate, json={"action": "exclude", "note": "Numbers wrong in scene 2"}, headers=csrf(admin)
        )
        assert out.status_code == 200 and out.json()["explore"]["reason"] == "excluded"
        assert explore_ids(c) == [] and c.get(lesson).json()["explore"]["excluded"] is True
        assert any("left Explore" in n["title"] for n in c.get("/api/v1/notifications").json()["items"])
        back = admin.put(curate, json={"action": "automatic"}, headers=csrf(admin))
        assert back.json()["explore"]["listed"] is True and explore_ids(c) == [lid]

        # The monitor.
        summary = admin.get("/api/v1/admin/ratings/summary").json()
        assert summary["ratings"] == 2 and summary["in_explore"]["auto"] == 1 and summary["meets_rule"] == 1
        assert summary["stars"] == [0, 0, 1, 0, 1] and summary["recent_comments"][0]["comment"] == "Fast"
        rule = admin.get("/api/v1/admin/ratings/lessons", params={"view": "rule"}).json()
        assert [x["id"] for x in rule["items"]] == [lid] and rule["items"][0]["rating"]["stars"] == [
            0,
            0,
            1,
            0,
            1,
        ]
        detail = admin.get(f"/api/v1/admin/ratings/lessons/{lid}").json()
        assert detail["ratings_total"] == 2 and all(x["counts"] for x in detail["ratings"])
        assert {"explore.exclude", "explore.automatic"} <= {h["action"] for h in detail["history"]}
        review = admin.put(
            f"/api/v1/admin/ratings/lessons/{lid}/review",
            json={"status": "flagged", "note": "Redo scene 2"},
            headers=csrf(admin),
        )
        assert review.status_code == 200
        flagged = admin.get("/api/v1/admin/ratings/lessons", params={"view": "review"}).json()["items"]
        assert flagged[0]["review"] == {
            "status": "flagged",
            "note": "Redo scene 2",
            "at": flagged[0]["review"]["at"],
        }
        # Learners cannot monitor or curate.
        assert c.get("/api/v1/admin/ratings/summary").status_code == 403
        assert c.put(curate, json={"action": "include"}, headers=csrf(c)).status_code == 403

        # An admin adds a private lesson only when told it will be shared with a link.
        second = c.post("/api/v1/lessons", json={"topic": "Pendulums"}, headers=csrf(c)).json()["id"]
        read_events(c, second)
        wait_for(c, second)
        add = f"/api/v1/admin/explore/{second}"
        assert admin.put(add, json={"action": "include"}, headers=csrf(admin)).status_code == 409
        added = admin.put(
            add, json={"action": "include", "share": True, "subject": "chemistry"}, headers=csrf(admin)
        )
        assert added.status_code == 200 and added.json()["visibility"] == "unlisted"
        assert explore_ids(c, subject="chemistry") == [second]
        assert c.get(f"/api/v1/lessons/{second}").json()["explore"]["source"] == "admin"
        # Its owner makes it private: it leaves Explore, and sharing it again does not bring it back.
        c.patch(f"/api/v1/lessons/{second}", json={"visibility": "private"}, headers=csrf(c))
        assert second not in explore_ids(c)
        c.patch(f"/api/v1/lessons/{second}", json={"visibility": "unlisted"}, headers=csrf(c))
        assert second not in explore_ids(c)

        # A rater closes their account: their rating goes, and the lesson falls below the rule.
        gone = raters[0].request(
            "DELETE", "/api/v1/me", json={"password": PASSWORD, "confirm": "DELETE"}, headers=csrf(raters[0])
        )
        assert gone.status_code == 204
        assert c.get(lesson).json()["rating_count"] == 1 and explore_ids(c) == []


# ---------------------------------------------------------------------- the migration


def test_the_migration_keeps_existing_records(tmp_path: Path) -> None:
    """0001 -> 0002 on a database with lessons: nothing lost, public lessons stay in Explore."""
    url = f"sqlite+aiosqlite:///{(tmp_path / 'p.db').as_posix()}"

    async def go() -> None:
        db = Database(url)

        def to(target: str, down: bool = False) -> Any:
            def run(conn: Connection) -> None:
                (command.downgrade if down else command.upgrade)(alembic_config(conn), target)

            return run

        async with db.engine.begin() as conn:
            await conn.run_sync(to("0001"))
        lesson = (
            "INSERT INTO lessons (id, title, subtitle, topic, audience, language, duration_min, planned_s, "
            "scene_count, status, subject, visibility, featured, cost_usd, views, rating_sum, rating_count, "
            "created_at, updated_at, completed_at) VALUES (:id, 't', '', 't', '', 'English', 3, 60, 4, "
            ":status, 'physics', :vis, 0, 0.1, 0, 0, 0, 1, 2, :done)"
        )
        async with db.engine.begin() as conn:
            for lid, status, vis in (
                ("g_000000000001", "complete", "public"),
                ("g_000000000002", "complete", "unlisted"),
                ("g_000000000003", "failed", "public"),
            ):
                await conn.execute(text(lesson), {"id": lid, "status": status, "vis": vis, "done": 5.0})
        await db.migrate()
        async with db.engine.connect() as conn:
            rows = (
                await conn.execute(
                    text("SELECT id, explore_listed, explore_source, explore_at FROM lessons ORDER BY id")
                )
            ).all()
        assert [(r[0][-1], bool(r[1]), r[2], r[3]) for r in rows] == [
            ("1", True, "owner", 5.0),
            ("2", False, "", None),
            ("3", False, "", None),
        ]
        async with db.engine.begin() as conn:
            await conn.run_sync(to("0001", down=True))
        async with db.engine.connect() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM lessons"))).scalar() == 3
        await db.close()

    asyncio.run(go())


def test_racing_posters_post_a_lesson_once(tmp_path: Path) -> None:
    """Two posters read the same lesson (two API processes, or a restart racing the sync): the one that
    moves the lesson's counter posts, the other posts nothing."""

    async def go() -> None:
        db = Database(f"sqlite+aiosqlite:///{(tmp_path / 'p.db').as_posix()}")
        await db.migrate()
        ps = PlatformSettings(_env_file=None)
        gen = Generation(request=GenerationRequest(topic="Why is the sky blue?"))
        gen.metrics.cost_usd = 0.05
        async with db.session() as s:
            s.add(User(id="u1", email="a@school.org", name="A", password_hash="x"))
            await s.flush()  # the owner first (no ORM relationship orders the inserts)
            s.add(Lesson(id=gen.id, owner_id="u1", topic="Why is the sky blue?"))
            await s.commit()
        async with db.session() as a, db.session() as b:
            first, second = await a.get(Lesson, gen.id), await b.get(Lesson, gen.id)
            assert first is not None and second is not None
            assert await usage.bill_lesson(a, ps, first, gen, 1.0) == 1
            await a.commit()
            assert await usage.bill_lesson(b, ps, second, gen, 1.0) == 0  # already posted by the other
            await b.commit()
            assert second.cost_billed_micros == 50_000
        async with db.session() as s:
            entries = (await s.scalars(select(UsageEntry))).all()
        assert [(e.kind, e.cost_micros, e.credits_milli) for e in entries] == [("lesson_ai", 50_000, 5_000)]
        await db.close()

    asyncio.run(go())
