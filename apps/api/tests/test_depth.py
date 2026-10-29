"""Lessons that fit their length (ADR-063): the depth by length, the coverage fitted into the time with
word budgets at the voice's pace, the reviewer's measured note, the trim, and the pace book."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from animator.pipeline.depth import (
    AIM,
    LEAST_SCENE_WORDS,
    apply_trim,
    budget_note,
    fit,
    over_budget,
    script_words,
    teacher_lines,
    tier_for,
    tiers_json,
)
from animator.pipeline.models import CoveragePlan, ScriptTrim, TeachingScript
from animator.pipeline.pace import LEAST, PaceBook

WPS, QUIET = 2.45, 1.1


def unit(
    uid: str, kind: str, priority: int, seconds: float, needs: list[str] | None = None
) -> dict[str, Any]:
    return {
        "id": uid,
        "kind": kind,
        "title": uid.replace("_", " ").capitalize(),
        "teach": f"What {uid} teaches.",
        "priority": priority,
        "seconds": seconds,
        "needs": needs or [],
    }


def floating() -> CoveragePlan:
    return CoveragePlan.model_validate(
        {
            "big_idea": "A fluid pushes up with the weight of the fluid moved aside.",
            "hook": "Why does a steel ship float when a nail sinks?",
            "units": [
                unit("density", "prerequisite", 3, 25),
                unit("push_ball", "intuition", 1, 20),
                unit("buoyant_force", "core", 1, 30, ["push_ball"]),
                unit("pressure", "mechanism", 2, 40, ["buoyant_force"]),
                unit("block_numbers", "example", 2, 35, ["buoyant_force"]),
                unit("heavy_sink", "misconception", 3, 25, ["buoyant_force"]),
                unit("ships", "application", 3, 25, ["buoyant_force"]),
                unit("balloon", "extension", 4, 25, ["buoyant_force"]),
                unit("submarine", "application", 4, 30, ["heavy_sink"]),
                unit("iceberg", "practice", 5, 50, ["block_numbers"]),
            ],
        }
    )


def chosen(minutes: float, plan: CoveragePlan | None = None) -> set[str]:
    return set(fit(plan or floating(), minutes * 60, WPS, QUIET).chosen)


def test_depth_grows_with_the_length() -> None:
    assert [tier_for(m * 60).id for m in (0.5, 1, 2, 3, 4, 5, 7, 8, 10)] == [
        "snapshot",
        "snapshot",
        "essentials",
        "deep_dive",
        "deep_dive",
        "mastery",
        "mastery",
        "masterclass",
        "masterclass",
    ]
    assert [t["name"] for t in tiers_json()] == [
        "Snapshot",
        "Essentials",
        "Deep dive",
        "Mastery",
        "Masterclass",
    ]
    assert tiers_json()[-1]["upto_s"] is None  # JSON has no infinity


def test_a_short_lesson_teaches_the_essentials_and_a_longer_one_adds_depth() -> None:
    one, two, three, five = chosen(1), chosen(2), chosen(3), chosen(5)
    assert one == {"push_ball", "buoyant_force"}  # the core idea and what makes it click
    assert {"push_ball", "buoyant_force", "block_numbers"} <= two and "heavy_sink" not in two
    assert {
        "pressure",
        "heavy_sink",
        "density",
    } <= three  # how it works, the misconception, what to know first
    assert one < two < three <= five and "iceberg" not in five  # practice: only the masterclass
    assert "iceberg" in chosen(10)


@pytest.mark.parametrize("minutes", [0.5, 1, 2, 3, 5, 8, 10])
def test_the_budget_fills_the_time_at_the_voices_pace(minutes: float) -> None:
    bp = fit(floating(), minutes * 60, WPS, QUIET)
    speech = minutes * 60 - len(bp.scenes) * QUIET
    assert bp.words <= speech * WPS * 1.02  # never longer than the time asked
    assert bp.words >= speech * WPS * 0.9 or minutes >= 8  # and fills it (a thin map: see below)
    assert all(s.words >= LEAST_SCENE_WORDS for s in bp.scenes)
    assert bp.scenes[0].step == "hook" and "land the lesson" in bp.scenes[-1].teach
    assert bp.most_words == round(bp.words * 1.05)
    # Units come after the units they build on.
    order = [s.unit for s in bp.scenes if s.unit]
    first = {u: order.index(u) for u in order}
    for u in floating().units:
        for need in u.needs:
            if u.id in first and need in first:
                assert first[need] < first[u.id]


def test_what_does_not_fit_is_left_for_a_longer_lesson() -> None:
    bp = fit(floating(), 60, WPS, QUIET)
    assert [u.id for u in bp.deeper][:2] == ["pressure", "block_numbers"]  # most important first
    assert set(bp.chosen) | {u.id for u in bp.deeper} == {u.id for u in floating().units}
    lines = "\n".join(teacher_lines(bp))
    assert "Depth: Snapshot, a 1-minute lesson" in lines
    assert "Hook: Why does a steel ship float" in lines and "The one idea to land: A fluid" in lines
    assert f"exactly {len(bp.scenes)} scenes" in lines and "Left for a longer lesson" in lines
    assert "Pressure" in lines.split("Left for a longer lesson")[1]


def test_one_minute_has_the_scenes_and_words_it_has_room_for() -> None:
    bp = fit(floating(), 60, WPS, QUIET)
    assert len(bp.scenes) == 4 and bp.checkpoints == 1
    assert 125 <= bp.words <= 140  # about 55 s of speech, not the 172 words of before (75 s)


def test_without_a_coverage_map_the_lesson_still_gets_its_depth_and_budget() -> None:
    bp = fit(None, 120, WPS, QUIET)
    assert bp.tier.id == "essentials" and bp.chosen == () and bp.deeper == ()
    assert len(bp.scenes) == 7 and bp.scenes[0].step == "hook" and bp.scenes[-1].step == "recap"
    assert bp.scenes[1].step is None  # the teacher chooses
    assert "your choice" in "\n".join(teacher_lines(bp))
    assert bp.words == pytest.approx((120 - 7 * QUIET) * WPS * AIM, abs=7)  # aimed a little under


def test_a_thin_map_makes_a_shorter_lesson_rather_than_a_padded_one() -> None:
    thin = CoveragePlan.model_validate(
        {"big_idea": "x", "hook": "y", "units": [unit("only", "core", 1, 20), unit("more", "example", 2, 20)]}
    )
    bp = fit(thin, 600, WPS, QUIET)
    assert bp.words <= (20 + 20 + 15 + 30) * 1.5 * WPS + len(bp.scenes)  # at most 1.5x (and rounding)


def test_a_first_unit_longer_than_the_lesson_is_squeezed_not_dropped() -> None:
    big = CoveragePlan.model_validate(
        {"big_idea": "x", "hook": "y", "units": [unit("huge", "core", 1, 180), unit("small", "core", 1, 15)]}
    )
    bp = fit(big, 60, WPS, QUIET)
    assert "huge" in bp.chosen
    assert bp.words <= (60 - len(bp.scenes) * QUIET) * WPS * 1.02


def test_the_models_numbers_are_not_trusted() -> None:
    odd = CoveragePlan.model_validate(
        {
            "big_idea": "x",
            "hook": "y",
            "units": [
                unit("a", "core", 0, -5, ["a", "b"]),  # priority, time and a need on itself
                unit("b", "intuition", 9, 9999, ["a"]),  # a loop
                unit("a", "core", 1, 10),  # a duplicate id
                {**unit("c", "example", 1, 10), "title": "  "},  # no title
            ],
        }
    )
    bp = fit(odd, 180, WPS, QUIET)
    assert sorted(u.id for u in bp.units) == ["a", "b"]
    assert bp.units[0].priority == 1 and bp.units[0].seconds == 8.0 and bp.units[0].needs == ["b"]
    assert bp.units[1].priority == 5 and bp.units[1].seconds == 180.0


def script_of(words: list[int]) -> TeachingScript:
    def beat(n: int) -> dict[str, str]:
        return {"say": " ".join(["word"] * n), "show": "a thing"}

    return TeachingScript.model_validate(
        {
            "title": "T",
            "subtitle": "S",
            "subject": "physics",
            "learner": "a student",
            "hook": "h",
            "learning_objectives": ["Explain x"],
            "core_idea": "c",
            "intuition": "i",
            "misconception": "m",
            "worked_example": None,
            "key_terms": [],
            "scenes": [
                {
                    "title": f"Scene {k}",
                    "step": "concept",
                    "goal": "g",
                    "beats": [beat(n // 2), beat(n - n // 2)],
                }
                for k, n in enumerate(words)
            ],
            "summary": ["s"],
        }
    )


def test_the_reviewer_sees_every_scenes_words_against_its_budget() -> None:
    bp = fit(floating(), 60, WPS, QUIET)
    budgets = [s.words for s in bp.scenes]
    long = script_of([b + 20 for b in budgets])
    note = budget_note(long, bp)
    assert f"the budget is {bp.words} (at most {bp.most_words})" in note
    assert f"1: {budgets[0] + 20} of {budgets[0]} (cut 20)" in note
    fine = budget_note(script_of(budgets), bp)
    assert "close to the budget" in fine and "(cut" not in fine
    short = budget_note(script_of([b // 2 for b in budgets]), bp)
    assert "Use the room" in short and "room for" in short
    other = budget_note(script_of([30, 30]), bp)
    assert f"The plan has {len(bp.scenes)} scenes; this script has 2" in other


def test_only_a_script_well_over_its_budget_is_trimmed_and_only_its_long_scenes() -> None:
    bp = fit(floating(), 60, WPS, QUIET)
    budgets = [s.words for s in bp.scenes]
    assert over_budget(script_of([round(b * 1.08) for b in budgets]), bp) == []  # a little over: kept
    script = script_of([budgets[0] * 2, budgets[1], budgets[2] * 2, budgets[3]])
    assert over_budget(script, bp) == [0, 2]
    shorter = [{"say": "A **short** one.", "show": "x"}, {"say": "Two.", "show": "y"}]
    longer = [{"say": " ".join(["more"] * 200), "show": "x"}]
    trim = ScriptTrim.model_validate(
        {
            "scenes": [
                {"scene": 1, "beats": shorter},
                {"scene": 2, "beats": shorter},  # not asked for
                {"scene": 3, "beats": longer},  # not shorter
                {"scene": 9, "beats": shorter},  # no such scene
            ]
        }
    )
    out, changed = apply_trim(script, trim, [0, 2])
    assert changed == 1 and out.scenes[0].beats[0].say == "A **short** one."
    assert out.scenes[1:] == script.scenes[1:] and script_words(out) < script_words(script)
    empty = ScriptTrim.model_validate({"scenes": [{"scene": 1, "beats": [{"say": " ", "show": "x"}]}]})
    assert apply_trim(script, empty, [0])[1] == 0  # a scene is never left without words


# ---------------------------------------------------------------------- the pace book


def test_the_pace_of_a_voice_is_measured_and_kept(tmp_path: Path) -> None:
    book = PaceBook(tmp_path / "pace.json", 2.45)
    key = PaceBook.key("elevenlabs", "v1", "Hinglish")
    assert key == "elevenlabs|v1|hinglish" and book.wps(key) == 2.45  # the default until measured
    for words, seconds in [(40, 20.0), (44, 20.0), (30, 10.0)][: LEAST - 1]:
        book.record(key, words, seconds)
    assert book.wps(key) == 2.45  # too few measurements yet
    book.record(key, 30, 10.0)
    assert book.wps(key) == pytest.approx(2.2)  # the median of 2.0, 2.2, 3.0
    book.record(key, 5, 1.0)  # a scene of five words says nothing
    book.record(key, 40, 4.0)  # 10 words a second is not speech
    book.record(key, 40, 0.0)
    assert book.samples(key) == LEAST
    again = PaceBook(tmp_path / "pace.json", 2.45)
    assert again.wps(key) == pytest.approx(2.2) and again.wps("other|v|english") == 2.45
    assert json.loads((tmp_path / "pace.json").read_text(encoding="utf-8"))[key] == [2.0, 2.2, 3.0]


def test_a_broken_or_missing_pace_book_is_the_default(tmp_path: Path) -> None:
    (tmp_path / "pace.json").write_text("{not json", encoding="utf-8")
    assert PaceBook(tmp_path / "pace.json", 2.3).wps("a|b|c") == 2.3
    memory = PaceBook(None, 2.5)
    for _ in range(LEAST):
        memory.record("k", 30, 10.0)
    assert memory.wps("k") == 3.0  # kept in memory when there is no file
