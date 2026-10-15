"""The app's color (ADR-057): a preference, crimson (the brand's red) unless the person picks another
of the avatar colors."""

from __future__ import annotations

from pathlib import Path

from tests.test_platform import csrf, make_app, register


def test_the_app_color_is_a_preference_with_the_brand_red_by_default(
    tmp_path: Path, worker_script: Path
) -> None:
    with make_app(tmp_path) as c:
        user = register(c, "asha@example.org")
        assert user["preferences"]["accent"] == "crimson"
        r = c.patch("/api/v1/me", json={"avatar": "teal", "preferences": {"accent": "teal"}}, headers=csrf(c))
        assert r.status_code == 200
        got = r.json()["user"]
        assert (got["avatar"], got["preferences"]["accent"]) == ("teal", "teal")
        assert c.get("/api/v1/auth/me").json()["user"]["preferences"]["accent"] == "teal"
        bad = c.patch("/api/v1/me", json={"preferences": {"accent": "neon"}}, headers=csrf(c))
        assert bad.status_code == 422
        # Other preferences keep it.
        r = c.patch("/api/v1/me", json={"preferences": {"theme": "dark"}}, headers=csrf(c))
        assert r.json()["user"]["preferences"]["accent"] == "teal"


def test_the_classic_colors_and_the_ivory_theme_are_preferences(tmp_path: Path, worker_script: Path) -> None:
    """ADR-062: tan, espresso, onyx, navy, olive and gold join the colors; ivory (warm paper) the themes."""
    with make_app(tmp_path) as c:
        register(c, "ravi@example.org")
        for accent in ("tan", "espresso", "onyx", "navy", "olive", "gold"):
            r = c.patch(
                "/api/v1/me", json={"avatar": accent, "preferences": {"accent": accent}}, headers=csrf(c)
            )
            assert r.status_code == 200, r.text
            got = r.json()["user"]
            assert (got["avatar"], got["preferences"]["accent"]) == (accent, accent)
        r = c.patch("/api/v1/me", json={"preferences": {"theme": "ivory"}}, headers=csrf(c))
        assert r.status_code == 200
        me = c.get("/api/v1/auth/me").json()["user"]["preferences"]
        assert (me["theme"], me["accent"]) == ("ivory", "gold")
        assert (
            c.patch("/api/v1/me", json={"preferences": {"theme": "sepia"}}, headers=csrf(c)).status_code
            == 422
        )
