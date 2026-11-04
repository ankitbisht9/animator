"""Content first (ADR-072): the content brief a subject expert prepares before the lesson is written, as
the teacher, the reviewer and the visual director are given it; the subject playbooks; and what code
measures in the narration's words (sentences too long to follow by ear, terms used too early)."""

from __future__ import annotations

from typing import Any, get_args

from animator.pipeline.content import (
    LONG_WORDS,
    brief_lines,
    early_terms,
    long_by_scene,
    long_sentences,
    longs_in,
    picture_lines,
    pictures_of,
    playbook_sections,
    word_note,
)
from animator.pipeline.models import ContentBrief, TeachingField, TeachingScript
from animator.pipeline.prompts import load_prompt


def brief(**over: Any) -> ContentBrief:
    data: dict[str, Any] = {
        "field": "networks_systems",
        "learner_start": "They send WhatsApp messages every day.",
        "takeaway": "Data is wrapped, carried hop by hop and unwrapped.",
        "terms": [
            {"term": "router", "meaning": "a box that reads the address and sends the packet on", "scene": 2},
            {"term": "port", "meaning": "the number that picks the app", "scene": 3},
        ],
        "scenes": [
            {
                "scene": k,
                "point": f"point {k}",
                "because": f"because {k}",
                "example": "Riya's message 'Meet at 6'",
                "analogy": "A router is like a post office: the IP address is the address on the letter. "
                "Unlike a post office it decides in microseconds."
                if k == 2
                else "",
                "misconception": "",
                "picture": f"picture {k}",
            }
            for k in (2, 1, 3)  # out of order: shown in scene order
        ],
        "worked": [],
        "contrast": [],
        "choose_when": "",
    }
    data.update(over)
    return ContentBrief.model_validate(data)


def script(*scenes: list[str]) -> TeachingScript:
    return TeachingScript.model_validate(
        {
            "title": "T",
            "subtitle": "S",
            "subject": "networks",
            "learner": "an adult",
            "hook": "h",
            "learning_objectives": ["Trace a packet"],
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
                    "beats": [{"say": s, "show": "x"} for s in says],
                }
                for k, says in enumerate(scenes)
            ],
            "summary": ["s"],
        }
    )


def test_every_subject_family_has_its_playbook() -> None:
    sections = playbook_sections(load_prompt("playbooks").text)
    assert set(sections) == set(get_args(TeachingField))  # the schema's families, no more, no less
    for name, section in sections.items():
        assert "Content:" in section and pictures_of(section), name
    assert "particle view" in pictures_of(sections["chemistry"])
    assert "straight up and down" in pictures_of(sections["physics"])  # never an arc for a vertical throw
    assert "Content:" not in pictures_of(sections["maths"])


def test_the_teacher_is_given_the_content_scene_by_scene() -> None:
    worked = [
        {
            "label": "Find the time to the top",
            "work": "0 = 20 - 10t, so t = 2 s",
            "reason": "at the top v is 0.",
        },
        {"label": "Check it", "work": "20 - 10 x 2 = 0", "reason": "the speed is zero at the top"},
    ]
    contrast = [{"feature": "Delivery", "first": "TCP resends what is lost", "second": "UDP does not"}]
    lines = "\n".join(
        brief_lines(brief(worked=worked, contrast=contrast, choose_when="TCP for files, UDP for calls"), "PB")
    )
    assert lines.startswith("Content brief (the lesson's content")
    assert "Subject family: networks_systems. The learner starts from: They send WhatsApp" in lines
    assert "router: a box that reads the address and sends the packet on (scene 2)" in lines
    assert lines.index("Scene 1. Point: point 1") < lines.index("Scene 2. Point: point 2")  # scene order
    assert (
        "Analogy: A router is like a post office" in lines and "Misconception:" not in lines
    )  # empty: left out
    assert "1. Find the time to the top: 0 = 20 - 10t, so t = 2 s (because at the top v is 0)." in lines
    assert "Delivery: TCP resends what is lost | UDP does not" in lines
    assert (
        "When to choose which: TCP for files" in lines and "How networks_systems is taught best:\nPB" in lines
    )
    assert "How networks_systems" not in "\n".join(brief_lines(brief(), None))  # the reviewer: no playbook


def test_the_director_is_given_the_pictures_and_the_terms_to_label() -> None:
    lines = "\n".join(picture_lines(brief(), "- icons in fixed places"))
    assert "Scene 1: picture 1 Scene 2: picture 2 Scene 3: picture 3" in lines
    assert "Terms to label on the picture as they are first said: router (scene 2); port (scene 3)" in lines
    assert "Pictures that teach networks_systems best:\n- icons in fixed places" in lines


def test_sentences_too_long_to_follow_by_ear_are_measured() -> None:
    long = " ".join(["word"] * (LONG_WORDS + 3)) + "."
    s = script(["A short **one**. " + long], ["Fine.", "Kya yeh sahi hai? " + long + " Done."])
    found = long_sentences(s)
    assert [(i, n) for i, _, n in found] == [(0, LONG_WORDS + 3), (1, LONG_WORDS + 3)]
    note = word_note(s, None)
    assert f"2 sentences are longer than {LONG_WORDS} words" in note and "scene 1:" in note
    assert "Terms used" not in note  # without a brief nothing to check them against
    clean = word_note(script(["Short.", "Also short."]), None)
    assert f"no sentence is longer than {LONG_WORDS} words" in clean


def test_terms_used_before_the_scene_that_explains_them_are_found() -> None:
    s = script(
        ["Riya's message leaves her **laptop**.", "The Router reads it."],  # router: before its scene (2)
        ["The router sends it on."],
        ["The port picks the app.", "A portal is not a port."],
    )
    assert early_terms(s, brief()) == [("router", 1, 2)]  # whole words only, any case; port is on time
    note = word_note(s, brief())
    assert "'router' first said in scene 1, explained in scene 2" in note


def test_the_content_writer_sees_every_playbook() -> None:
    text = load_prompt("content").text
    assert "{playbooks}" in text
    sections = playbook_sections(load_prompt("playbooks").text)
    assert len(sections) == len(get_args(TeachingField))


def test_long_sentences_are_listed_by_scene_and_a_split_is_kept_only_when_it_helps() -> None:
    from animator.pipeline.depth import apply_trim, fit, questions_in
    from animator.pipeline.models import ScriptTrim

    long = " ".join(["word"] * (LONG_WORDS + 2)) + "."
    s = script(["Fine."], ["Short one. " + long])
    assert long_by_scene(s) == {1: [long]} and longs_in(s.scenes[1]) == 1
    halves = [{"say": " ".join(["word"] * 12) + ". " + " ".join(["word"] * 12) + ".", "show": "x"}]
    same = [{"say": "Short one. " + long, "show": "x"}]
    for beats, kept in ((halves, True), (same, False)):
        trim = ScriptTrim.model_validate({"scenes": [{"scene": 2, "beats": beats}]})
        out, changed = apply_trim(s, trim, [1], None, long_by_scene(s))
        assert changed == int(kept) and (longs_in(out.scenes[1]) == 0) == kept
    # A quoted message is not a question to the learner.
    asked = script(['Riya sends "Reached home?" to Aman.', "Is it there yet?"])
    assert questions_in(asked) == [(0, "Is it there yet?")]  # one scene: the quoted one is skipped
    assert fit(None, 60, 2.5, 1.0, flow=True).new_terms == 2
