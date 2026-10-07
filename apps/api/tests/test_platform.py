"""The platform (ADR-051): accounts and sessions, the gateway's rules over the pipeline's routes,
lessons with owners, sharing, learning records, doubts through the gateway, support, administration,
and the switch that turns it all off. The pipeline runs for real (engine worker, scripted model)."""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from animator.api.app import create_app
from animator.jobs.store import MemoryStore
from animator.llm import FakeLLM
from animator.pipeline.models import Generation, GenerationRequest
from animator.platform import PlatformSettings
from animator.platform import catalog as catalog_module
from animator.platform.api.learning import _streaks
from animator.platform.catalog import subject_of
from animator.platform.ratelimit import RateLimiter
from animator.platform.security import issue_access, password_problem, read_access
from tests.test_doubts import BOARD, Tutor
from tests.test_generations_api import read_events
from tests.test_pipeline import Script, settings

PASSWORD = "Lessons-are-fun-42"


@pytest.fixture(autouse=True)
def quick_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog_module, "SYNC_EVERY_S", 0.1)


def platform_settings(tmp: Path, **kw: Any) -> PlatformSettings:
    base: dict[str, Any] = {
        "data_dir": tmp / "platform",
        "admin_emails": "admin@school.org",
        "public_base_url": "http://testserver",
        "first_user_admin": False,
    }
    base.update(kw)
    return PlatformSettings(_env_file=None, **base)


def make_app(tmp: Path, llm: Any = None, store: MemoryStore | None = None, **kw: Any) -> TestClient:
    s = settings(openai_api_key="sk-proj-secret-value-that-must-never-leak")
    app = create_app(
        settings=s,
        llm=FakeLLM(llm or Script()),
        store=store or MemoryStore(),
        resume_on_start=False,
        platform=platform_settings(tmp, **kw),
    )
    return TestClient(app)


def forget_lesson(c: TestClient, lesson_id: str) -> None:
    """Sharing changed: drop the gateway's few-second cache of who may read the lesson."""
    app = cast(FastAPI, c.app)
    app.state.platform.forget_lesson(lesson_id)


def csrf(c: TestClient) -> dict[str, str]:
    return {"x-csrf-token": c.cookies.get("anim_csrf") or ""}


def register(c: TestClient, email: str, name: str = "Asha Rao", password: str = PASSWORD) -> dict[str, Any]:
    r = c.post("/api/v1/auth/register", json={"name": name, "email": email, "password": password})
    assert r.status_code == 201, r.text
    user: dict[str, Any] = r.json()["user"]
    return user


def other(app_client: TestClient) -> TestClient:
    """A second browser on the same app (its own cookies), on the app's own event loop."""
    t = TestClient(app_client.app)
    t.portal = app_client.portal
    return t


def wait_for(c: TestClient, lesson_id: str, status: str = "complete", timeout: float = 20) -> dict[str, Any]:
    end = time.time() + timeout
    while time.time() < end:
        got: dict[str, Any] = c.get(f"/api/v1/lessons/{lesson_id}").json()
        if got.get("status") == status:
            return got
        time.sleep(0.1)
    raise AssertionError(f"lesson {lesson_id} never became {status}")


# ---------------------------------------------------------------------- units


def test_password_policy() -> None:
    assert password_problem("short1") is not None
    assert password_problem("allletters") is not None
    assert password_problem("password123") is not None  # common
    assert password_problem("asharao2024!", email="asharao@x.org") is not None  # their own email
    assert password_problem(PASSWORD, email="asha@x.org", name="Asha Rao") is None


def test_access_tokens_are_signed_and_expire() -> None:
    token = issue_access("k" * 40, "u1", "s1", "learner", 60)
    claims = read_access("k" * 40, token)
    assert claims is not None and claims.user_id == "u1" and claims.session_id == "s1"
    assert read_access("x" * 40, token) is None  # another key
    assert read_access("k" * 40, token[:-3] + "abc") is None  # tampered
    assert read_access("k" * 40, issue_access("k" * 40, "u1", "s1", "learner", -60)) is None  # expired


def test_rate_limiter_windows() -> None:
    limiter = RateLimiter()
    assert all(limiter.hit("k", 3, 60) is None for _ in range(3))
    wait = limiter.hit("k", 3, 60)
    assert wait is not None and 0 < wait <= 60
    assert limiter.hit("other", 3, 60) is None


def test_subjects_and_streaks() -> None:
    assert subject_of("Explain Archimedes' principle and buoyancy") == "physics"
    assert subject_of("How does photosynthesis work?") == "biology"
    assert subject_of("How does binary search find a number so fast?") == "computer-science"
    assert subject_of("Something else entirely") == "general"
    today = dt.date(2026, 10, 7)
    days = {"2026-10-07": 120.0, "2026-10-06": 90.0, "2026-10-05": 30.0, "2026-10-01": 60, "2026-10-02": 60}
    assert _streaks(days, today) == (2, 2)  # the 5th had under a minute
    assert _streaks({"2026-10-06": 61.0}, today) == (1, 1)  # yesterday still counts until today ends


# ---------------------------------------------------------------------- accounts


def test_sign_up_sign_in_refresh_and_sign_out(tmp_path: Path, worker_script: Path) -> None:
    with make_app(tmp_path) as c:
        me = register(c, "Asha@Example.org")
        assert me["email"] == "asha@example.org" and me["role"] == "learner" and not me["verified"]
        assert "lesson:create" in me["permissions"] and "user:manage" not in me["permissions"]
        set_cookie = c.post("/api/v1/auth/refresh").headers.get_list("set-cookie")
        access = next(h for h in set_cookie if h.startswith("anim_at="))
        assert "HttpOnly" in access and "SameSite=lax" in access
        refresh = next(h for h in set_cookie if h.startswith("anim_rt="))
        assert "HttpOnly" in refresh and "Path=/api/v1/auth" in refresh and "SameSite=strict" in refresh

        # The same email cannot sign up twice; a weak password is refused with a reason.
        again = c.post(
            "/api/v1/auth/register", json={"name": "A", "email": "asha@example.org", "password": PASSWORD}
        )
        assert again.status_code == 409
        weak = other(c).post(
            "/api/v1/auth/register", json={"name": "B", "email": "b@x.org", "password": "abc"}
        )
        assert weak.status_code == 422 and "characters" in weak.json()["detail"]

        assert c.get("/api/v1/auth/me").status_code == 200
        assert c.post("/api/v1/auth/logout").status_code == 204
        assert c.get("/api/v1/auth/me").status_code == 401

        bad = c.post("/api/v1/auth/login", json={"email": "asha@example.org", "password": "Wrong-password-1"})
        assert bad.status_code == 401 and bad.json()["detail"] == "Email or password is incorrect."
        nobody = c.post(
            "/api/v1/auth/login", json={"email": "nobody@example.org", "password": "Wrong-password-1"}
        )
        assert nobody.status_code == 401 and nobody.json()["detail"] == bad.json()["detail"]
        ok = c.post("/api/v1/auth/login", json={"email": "ASHA@example.org", "password": PASSWORD})
        assert ok.status_code == 200 and ok.json()["user"]["email"] == "asha@example.org"

        # Refresh rotates the token; replaying an old one (after the grace window) ends the session.
        old = c.cookies.get("anim_rt", path="/api/v1/auth")
        assert c.post("/api/v1/auth/refresh").status_code == 200
        assert c.cookies.get("anim_rt", path="/api/v1/auth") != old
        thief = other(c)
        stolen = {"cookie": f"anim_rt={old}"}
        assert (
            thief.post("/api/v1/auth/refresh", headers=stolen).status_code == 200
        )  # a racing tab, within grace
        from animator.platform.api import auth as auth_module

        auth_module.REFRESH_GRACE_S = -1
        try:
            assert (
                thief.post("/api/v1/auth/refresh", headers=stolen).status_code == 401
            )  # replayed later: stolen
        finally:
            auth_module.REFRESH_GRACE_S = 30
        assert c.post("/api/v1/auth/refresh").status_code == 401  # and the real session is over too
        audit = [e["action"] for e in admin_client(c).get("/api/v1/admin/audit").json()["items"]]
        assert "session.reused" in audit and "login.failed" in audit


def admin_client(c: TestClient) -> TestClient:
    a = other(c)
    r = a.post("/api/v1/auth/login", json={"email": "admin@school.org", "password": PASSWORD})
    if r.status_code != 200:
        register(a, "admin@school.org", "Head Teacher")
    return a


def test_lockout_after_repeated_failures(tmp_path: Path, worker_script: Path) -> None:
    with make_app(tmp_path, max_failed_logins=3, lockout_minutes=5) as c:
        register(c, "ravi@example.org", "Ravi")
        c.post("/api/v1/auth/logout")
        for _ in range(3):
            assert (
                c.post(
                    "/api/v1/auth/login", json={"email": "ravi@example.org", "password": "Nope-12345"}
                ).status_code
                == 401
            )
        locked = c.post("/api/v1/auth/login", json={"email": "ravi@example.org", "password": PASSWORD})
        assert locked.status_code == 429 and "minute" in locked.json()["detail"]


def test_password_reset_and_email_verification_by_mail(tmp_path: Path, worker_script: Path) -> None:
    with make_app(tmp_path) as c:
        register(c, "meera@example.org", "Meera")
        outbox = tmp_path / "platform" / "outbox"
        verify = next(json.loads(f.read_text()) for f in outbox.iterdir() if "Confirm" in f.read_text())
        token = verify["text"].split("token=")[1].split()[0]
        assert c.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 200
        assert c.get("/api/v1/auth/me").json()["user"]["verified"] is True
        assert c.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 400  # used once

        anon = other(c)
        same = anon.post("/api/v1/auth/password/forgot", json={"email": "nobody@example.org"})
        sent = anon.post("/api/v1/auth/password/forgot", json={"email": "meera@example.org"})
        assert same.status_code == sent.status_code == 202 and same.json() == sent.json()  # no enumeration
        mail = next(
            json.loads(f.read_text()) for f in outbox.iterdir() if "Reset your password" in f.read_text()
        )
        reset_token = mail["text"].split("token=")[1].split()[0]
        assert (
            anon.post(
                "/api/v1/auth/password/reset", json={"token": reset_token, "password": "abc"}
            ).status_code
            == 422
        )
        new = "Brand-new-password-7"
        assert (
            anon.post("/api/v1/auth/password/reset", json={"token": reset_token, "password": new}).status_code
            == 200
        )
        assert c.get("/api/v1/auth/me").status_code == 401  # every device signed out
        assert (
            anon.post("/api/v1/auth/login", json={"email": "meera@example.org", "password": new}).status_code
            == 200
        )


def test_profile_preferences_devices_and_closing_the_account(tmp_path: Path, worker_script: Path) -> None:
    with make_app(tmp_path) as c:
        register(c, "zoe@example.org", "Zoe")
        r = c.patch(
            "/api/v1/me",
            json={
                "bio": "Curious",
                "onboarded": True,
                "preferences": {"language": "Hinglish", "interests": ["physics", "x"]},
            },
            headers=csrf(c),
        )
        assert r.status_code == 200, r.text
        user = r.json()["user"]
        assert user["onboarded"] and user["preferences"]["language"] == "Hinglish"
        assert user["preferences"]["interests"] == ["physics"]  # unknown subjects dropped
        assert (
            c.patch("/api/v1/me", json={"role": "admin"}, headers=csrf(c)).status_code == 422
        )  # not settable

        laptop = other(c)
        laptop.post("/api/v1/auth/login", json={"email": "zoe@example.org", "password": PASSWORD})
        devices = c.get("/api/v1/me/sessions").json()
        assert len(devices) == 2 and sum(d["current"] for d in devices) == 1
        assert c.post("/api/v1/me/sessions/revoke-others", headers=csrf(c)).json()["signed_out"] == 1
        assert laptop.get("/api/v1/auth/me").status_code == 401

        wrong = c.post(
            "/api/v1/me/password", json={"current": "nope", "new": "Another-pass-99"}, headers=csrf(c)
        )
        assert wrong.status_code == 400
        assert (
            c.post(
                "/api/v1/me/password", json={"current": PASSWORD, "new": "Another-pass-99"}, headers=csrf(c)
            ).status_code
            == 200
        )

        data = c.get("/api/v1/me/export")
        assert data.status_code == 200 and data.json()["profile"]["email"] == "zoe@example.org"
        gone = c.request(
            "DELETE", "/api/v1/me", json={"password": "Another-pass-99", "confirm": "DELETE"}, headers=csrf(c)
        )
        assert gone.status_code == 204
        assert (
            c.post(
                "/api/v1/auth/login", json={"email": "zoe@example.org", "password": "Another-pass-99"}
            ).status_code
            == 401
        )


# ---------------------------------------------------------------------- the gateway


def test_the_pipeline_routes_are_guarded(tmp_path: Path, worker_script: Path) -> None:
    with make_app(tmp_path) as c:
        assert c.get("/api/health").status_code == 200  # public
        r = c.get("/api/generations")
        assert r.status_code == 401 and r.json()["request_id"] == r.headers["x-request-id"]
        assert c.get("/api/config").status_code == 401
        assert c.get("/api/something-new").status_code == 401  # unknown API routes: admins only
        assert c.get("/docs").status_code == 401

        register(c, "learner@example.org")
        assert c.get("/api/config").status_code == 200
        assert c.get("/api/generations").status_code == 403
        raw = c.post("/api/generations", json={"topic": "Archimedes' principle"}, headers=csrf(c))
        assert raw.status_code == 403  # the pipeline's own API is for admins; learners use /api/v1
        assert c.get("/api/generations/g_000000000000/events").status_code == 404
        assert c.get("/docs").status_code == 403

        # Security headers and request ids on every response.
        h = c.get("/api/v1/auth/me").headers
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
        assert h["cache-control"] == "no-store" and len(h["x-request-id"]) >= 6
        assert (
            c.get("/api/health", headers={"x-request-id": "trace-123456"}).headers["x-request-id"]
            == "trace-123456"
        )

        # CSRF: a cookie-signed change without the token is refused; a cross-site origin too.
        assert c.patch("/api/v1/me", json={"bio": "x"}).status_code == 403
        assert (
            c.patch(
                "/api/v1/me", json={"bio": "x"}, headers={**csrf(c), "origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert c.patch("/api/v1/me", json={"bio": "x"}, headers=csrf(c)).status_code == 200
        # A script with a bearer token needs no CSRF token (no cookies involved).
        token = c.cookies.get("anim_at")
        bare = other(c)
        assert (
            bare.patch(
                "/api/v1/me", json={"bio": "y"}, headers={"authorization": f"Bearer {token}"}
            ).status_code
            == 200
        )

        # Body size limit.
        big = c.post(
            "/api/v1/support/tickets",
            content=b"x" * 1_100_000,
            headers={**csrf(c), "content-type": "application/json"},
        )
        assert big.status_code == 413

        admin = admin_client(c)
        assert admin.get("/api/generations").status_code == 200
        assert admin.get("/docs").status_code == 200


def test_rate_limit(tmp_path: Path, worker_script: Path) -> None:
    with make_app(tmp_path, rate_limit_per_min=5) as c:
        codes = [c.get("/api/v1/public/info").status_code for _ in range(7)]
        assert codes[:5] == [200] * 5 and codes[-1] == 429


# ---------------------------------------------------------------------- lessons


def test_a_lesson_from_creation_to_sharing_and_learning(tmp_path: Path, worker_script: Path) -> None:
    with make_app(tmp_path, lessons_per_day_learner=1) as c:
        register(c, "asha@example.org")
        assert (
            c.post(
                "/api/v1/lessons",
                json={"topic": "Archimedes' principle", "duration_min": 25},
                headers=csrf(c),
            ).status_code
            == 422
        )
        pub = c.post(
            "/api/v1/lessons",
            json={"topic": "Archimedes' principle", "visibility": "public"},
            headers=csrf(c),
        )
        assert pub.status_code == 403  # learners share with a link; publishing is for educators
        r = c.post(
            "/api/v1/lessons", json={"topic": "Archimedes' principle", "duration_min": 3}, headers=csrf(c)
        )
        assert r.status_code == 201, r.text
        lesson = r.json()
        lid = lesson["id"]
        assert lesson["mine"] and lesson["subject"] == "physics" and lesson["visibility"] == "private"
        again = c.post("/api/v1/lessons", json={"topic": "Pendulums"}, headers=csrf(c))
        assert again.status_code == 429  # the daily quota (1 in this test)

        events = read_events(c, lid)  # the owner follows it through the pipeline's own stream
        assert events[-1]["generation"]["status"] == "complete"
        done = wait_for(c, lid)
        assert done["title"] == "Why things float" and done["scene_count"] == 4 and done["length_s"] > 0
        notes = c.get("/api/v1/notifications").json()
        assert any(n["kind"] == "lesson_ready" and n["link"] == f"/lessons/{lid}" for n in notes["items"])

        # Someone else cannot see it until it is shared with a link.
        ravi = other(c)
        register(ravi, "ravi@example.org", "Ravi")
        assert ravi.get(f"/api/v1/lessons/{lid}").status_code == 404
        assert ravi.get(f"/api/generations/{lid}/events").status_code == 404
        assert (
            c.patch(f"/api/v1/lessons/{lid}", json={"visibility": "unlisted"}, headers=csrf(c)).status_code
            == 200
        )
        forget_lesson(c, lid)
        assert ravi.get(f"/api/v1/lessons/{lid}").json()["mine"] is False
        assert len(read_events(ravi, lid)) >= 1
        assert (
            ravi.post(f"/api/generations/{lid}/cancel", headers=csrf(ravi)).status_code == 403
        )  # not theirs
        assert ravi.get(f"/api/generations/{lid}").status_code == 403
        assert (
            ravi.patch(
                f"/api/v1/lessons/{lid}", json={"visibility": "private"}, headers=csrf(ravi)
            ).status_code
            == 403
        )
        anon = other(c)
        assert len(read_events(anon, lid)) >= 1  # a shared link plays for anyone

        # Explore lists public lessons only; an educator can publish.
        assert c.get("/api/v1/explore").json()["items"] == []
        admin = admin_client(c)
        me = c.get("/api/v1/auth/me").json()["user"]
        assert (
            admin.patch(
                f"/api/v1/admin/users/{me['id']}", json={"role": "educator"}, headers=csrf(admin)
            ).status_code
            == 200
        )
        assert (
            c.patch(f"/api/v1/lessons/{lid}", json={"visibility": "public"}, headers=csrf(c)).status_code
            == 200
        )
        listed = c.get("/api/v1/explore", params={"subject": "physics"}).json()["items"]
        assert [x["id"] for x in listed] == [lid]
        assert (
            anon.get("/api/v1/explore").json()["items"][0]["cost_usd"] is None
        )  # cost is the owner's business

        # Learning records: progress, streak, notes, saved, rating.
        today = dt.date.today().isoformat()
        p1 = ravi.put(
            f"/api/v1/lessons/{lid}/progress",
            json={"position_s": 10, "duration_s": 100, "delta_s": 10, "day": today},
            headers=csrf(ravi),
        )
        assert p1.status_code == 200 and not p1.json()["completed"]
        time.sleep(0.05)
        p2 = ravi.put(
            f"/api/v1/lessons/{lid}/progress",
            json={
                "position_s": done["length_s"],
                "duration_s": done["length_s"],
                "delta_s": 600,
                "day": today,
            },
            headers=csrf(ravi),
        )
        assert p2.json()["completed"] and p2.json()["newly_completed"]
        stats = ravi.get("/api/v1/me/stats", params={"today": today}).json()
        assert stats["lessons_completed"] == 1
        assert stats["minutes_total"] < 2  # watched time cannot outrun the clock (600 s claimed)
        assert any(a["key"] == "finisher" and a["unlocked"] for a in stats["achievements"])
        n = ravi.post(
            f"/api/v1/lessons/{lid}/notes",
            json={"t_s": 4.5, "text": "Weight = buoyant force"},
            headers=csrf(ravi),
        )
        assert n.status_code == 201
        assert ravi.get(f"/api/v1/lessons/{lid}/notes").json()[0]["text"] == "Weight = buoyant force"
        assert c.get(f"/api/v1/lessons/{lid}/notes").json() == []  # notes are private
        assert c.request("DELETE", f"/api/v1/notes/{n.json()['id']}", headers=csrf(c)).status_code == 404
        assert ravi.put(f"/api/v1/lessons/{lid}/save", headers=csrf(ravi)).status_code == 204
        assert [x["id"] for x in ravi.get("/api/v1/me/library").json()["items"]] == [lid]
        rated = ravi.put(
            f"/api/v1/lessons/{lid}/rating", json={"stars": 5, "comment": "Clear!"}, headers=csrf(ravi)
        )
        assert rated.json()["rating"] == 5
        dash = ravi.get("/api/v1/me/learning").json()
        assert dash["completed"][0]["id"] == lid and dash["completed"][0]["progress"]["completed"]
        assert c.get("/api/v1/lessons", params={"q": "float"}).json()["items"][0]["views"] == 1

        # Deleting hides it everywhere (the lesson's files stay).
        assert c.request("DELETE", f"/api/v1/lessons/{lid}", headers=csrf(c)).status_code == 204
        forget_lesson(c, lid)
        assert ravi.get(f"/api/v1/lessons/{lid}").status_code == 404
        assert ravi.get(f"/api/generations/{lid}/events").status_code == 404
        assert c.get("/api/v1/lessons").json()["items"] == []


def test_doubts_through_the_gateway(tmp_path: Path, worker_script: Path) -> None:
    tutor = Tutor()
    with make_app(tmp_path, llm=tutor, doubts_per_day_learner=2) as c:
        register(c, "asha@example.org")
        lid = c.post("/api/v1/lessons", json={"topic": "Archimedes' principle"}, headers=csrf(c)).json()["id"]
        read_events(c, lid)
        tutor.doubts = [BOARD, BOARD, BOARD]
        ask = {"question": "What is density?", "scene_index": 0, "t": 1.0}
        r = c.post(f"/api/doubts/{lid}", json=ask, headers=csrf(c))
        assert r.status_code == 200, r.text
        sid = r.json()["session_id"]
        deadline = time.time() + 5
        while time.time() < deadline and not c.get("/api/v1/me/doubts").json():
            time.sleep(0.05)
        mine = c.get("/api/v1/me/doubts").json()
        assert mine[0]["session_id"] == sid and mine[0]["question"] == "What is density?"

        # Another learner cannot read or continue someone's doubt, even on a shared lesson.
        c.patch(f"/api/v1/lessons/{lid}", json={"visibility": "unlisted"}, headers=csrf(c))
        forget_lesson(c, lid)
        ravi = other(c)
        register(ravi, "ravi@example.org", "Ravi")
        assert ravi.get(f"/api/doubts/{lid}/{sid}").status_code == 404
        follow = ravi.post(f"/api/doubts/{lid}", json={**ask, "session_id": sid}, headers=csrf(ravi))
        assert follow.status_code == 404
        assert c.get(f"/api/doubts/{lid}/{sid}").status_code == 200
        # The daily quota (2 answers in this test).
        assert (
            c.post(f"/api/doubts/{lid}", json={**ask, "session_id": sid}, headers=csrf(c)).status_code == 200
        )
        time.sleep(0.3)
        over = c.post(f"/api/doubts/{lid}", json=ask, headers=csrf(c))
        assert over.status_code == 429 and "today" in over.json()["detail"]


# ---------------------------------------------------------------------- support and administration


def test_support_tickets(tmp_path: Path, worker_script: Path) -> None:
    with make_app(tmp_path) as c:
        admin = admin_client(c)
        register(c, "asha@example.org")
        t = c.post(
            "/api/v1/support/tickets",
            json={
                "subject": "Voice is silent",
                "category": "technical",
                "message": "No voice on the second scene.",
            },
            headers=csrf(c),
        )
        assert t.status_code == 201, t.text
        tid = t.json()["id"]
        assert any(n["kind"] == "ticket_new" for n in admin.get("/api/v1/notifications").json()["items"])
        queue = admin.get("/api/v1/admin/tickets").json()
        assert queue["total"] == 1 and queue["items"][0]["name"] == "Asha Rao"
        reply = admin.post(
            f"/api/v1/support/tickets/{tid}/messages", json={"body": "Try reloading."}, headers=csrf(admin)
        )
        assert reply.status_code == 201 and reply.json()["status"] == "pending"
        thread = c.get(f"/api/v1/support/tickets/{tid}").json()
        assert [m["staff"] for m in thread["messages"]] == [False, True]
        assert any(n["kind"] == "ticket_reply" for n in c.get("/api/v1/notifications").json()["items"])
        assert (
            admin.patch(
                f"/api/v1/admin/tickets/{tid}", json={"status": "resolved"}, headers=csrf(admin)
            ).status_code
            == 200
        )
        ravi = other(c)
        register(ravi, "ravi@example.org", "Ravi")
        assert ravi.get(f"/api/v1/support/tickets/{tid}").status_code == 404
        assert ravi.get("/api/v1/admin/tickets").status_code == 403


def test_administration(tmp_path: Path, worker_script: Path) -> None:
    with make_app(tmp_path) as c:
        admin = admin_client(c)
        me = admin.get("/api/v1/auth/me").json()["user"]
        assert me["role"] == "admin" and "user:manage" in me["permissions"]
        register(c, "asha@example.org")
        overview = admin.get("/api/v1/admin/overview").json()
        assert overview["users"] == 2 and overview["users_by_role"] == {"admin": 1, "learner": 1}
        assert len(overview["daily"]) == 14
        users = admin.get("/api/v1/admin/users", params={"q": "asha"}).json()
        assert users["total"] == 1
        uid = users["items"][0]["id"]
        assert (
            admin.patch(
                f"/api/v1/admin/users/{me['id']}", json={"role": "learner"}, headers=csrf(admin)
            ).status_code
            == 409
        )
        assert (
            admin.patch(
                f"/api/v1/admin/users/{uid}", json={"status": "suspended"}, headers=csrf(admin)
            ).status_code
            == 200
        )
        assert c.get("/api/v1/auth/me").status_code == 401  # suspended: signed out at once
        assert (
            c.post("/api/v1/auth/login", json={"email": "asha@example.org", "password": PASSWORD}).status_code
            == 403
        )
        system = admin.get("/api/v1/admin/system").json()
        assert system["database"] == "SQLite" and all(system["engine_workers"])
        assert c.get("/api/v1/admin/overview").status_code == 401
        actions = {e["action"] for e in admin.get("/api/v1/admin/audit").json()["items"]}
        assert {"user.register", "user.update"} <= actions


def test_lessons_made_before_accounts_go_to_the_first_admin(tmp_path: Path, worker_script: Path) -> None:
    store = MemoryStore()
    old = Generation(request=GenerationRequest(topic="How does photosynthesis work?"))
    old.status = "complete"

    import asyncio

    asyncio.run(store.save(old))
    with make_app(tmp_path, store=store) as c:
        deadline = time.time() + 5
        admin = None
        while time.time() < deadline:
            admin = admin_client(c)
            items = admin.get("/api/v1/lessons").json()["items"]
            if items:
                break
            time.sleep(0.1)
        assert admin is not None
        items = admin.get("/api/v1/lessons").json()["items"]
        assert [x["id"] for x in items] == [old.id] and items[0]["subject"] == "biology"


def test_the_platform_can_be_turned_off(tmp_path: Path, worker_script: Path) -> None:
    with make_app(tmp_path, enabled=False) as c:
        assert c.get("/api/v1/public/info").status_code == 404
        assert c.get("/api/generations").status_code == 200  # the API as before accounts
        assert "x-request-id" not in c.get("/api/health").headers


def test_first_account_runs_a_new_platform_in_development(tmp_path: Path, worker_script: Path) -> None:
    with make_app(tmp_path, first_user_admin=None, admin_emails="") as c:
        assert register(c, "owner@school.org", "Owner")["role"] == "admin"  # nobody else yet
        second = other(c)
        assert register(second, "next@school.org", "Next")["role"] == "learner"
    with make_app(tmp_path / "prod", first_user_admin=None, admin_emails="", environment="production") as c:
        # Production: no account is an admin by arriving first (use create-admin or PLATFORM_ADMIN_EMAILS).
        c.base_url = c.base_url.copy_with(scheme="https")
        r = c.post("/api/v1/auth/register", json={"name": "A", "email": "a@school.org", "password": PASSWORD})
        assert r.status_code == 201 and r.json()["user"]["role"] == "learner"
        assert "Secure" in r.headers["set-cookie"]


def test_local_origins_in_development_only(tmp_path: Path, worker_script: Path) -> None:
    with make_app(tmp_path) as c:
        register(c, "asha@example.org")
        local = {**csrf(c), "origin": "http://127.0.0.1:5173"}
        assert c.patch("/api/v1/me", json={"bio": "x"}, headers=local).status_code == 200
        far = {**csrf(c), "origin": "https://example.net"}
        assert c.patch("/api/v1/me", json={"bio": "x"}, headers=far).status_code == 403
