"""Drawing packs (ADR-066): open-licensed drawings shipped beside the built-in library, each with its
author and licence, renamed or retired like any built-in drawing, credited where the licence asks."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from animator.objects.models import Art, SavedObject, Shape, Source
from animator.objects.packs import credits, is_thin, load_packs, packs_info, with_art
from animator.objects.store import ObjectStore
from animator.pipeline.models import Generation, GenerationRequest, SceneRecord, StoryboardScene


def drawing(oid: str, license: str | None, author: str = "Servier") -> SavedObject:
    art = Art(
        w=400,
        h=300,
        shapes=[
            Shape(d="M4 4H396V296H4Z", fill="#e0453a"),
            Shape(d="M50 50H150V150H50Z", fill="#ffffff"),
            Shape(d="M200 50H300V150H200Z", fill="#2f6fdb"),
        ],
    )
    return SavedObject(
        id=oid,
        title=oid.replace("_", " ").capitalize(),
        concept=oid.replace("_", " "),
        aliases=[oid.replace("_", " ")],
        context=["body"],
        art=art,
        status="approved",
        origin="library",
        source=Source(
            kind="library",
            pack="bioicons",
            author=author,
            license=license,
            license_url="https://creativecommons.org/licenses/by/3.0/" if license else None,
            url=f"https://example.org/{oid}.svg",
        ),
    )


def write_pack(folder: Path, objs: list[Any]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"pack": {"name": "bioicons", "title": "Bioicons"}})] + [json.dumps(o) for o in objs]
    path = folder / "bioicons.jsonl.gz"
    path.write_bytes(gzip.compress("\n".join(lines).encode()))
    return path


def test_a_pack_is_read_and_a_bad_drawing_in_it_is_left_out(tmp_path: Path) -> None:
    good = drawing("kidney_section", "CC BY 3.0").model_dump()
    write_pack(
        tmp_path / "packs", [good, {"id": "Bad Id", "title": ""}, {**good, "id": "x2", "origin": "harvest"}]
    )
    objs, errors = load_packs(tmp_path / "packs")
    assert [o.id for o in objs] == ["kidney_section"]
    assert len(errors) == 2 and all(e.startswith("packs/bioicons.jsonl.gz") for e in errors)
    assert packs_info(tmp_path / "packs") == [{"name": "bioicons", "title": "Bioicons", "drawings": 1}]
    assert load_packs(tmp_path / "missing") == ([], [])


def test_pack_drawings_are_built_in_renamed_or_retired_never_deleted(tmp_path: Path) -> None:
    write_pack(tmp_path / "packs", [drawing("kidney_section", "CC BY 3.0").model_dump()])
    store = ObjectStore(tmp_path / "objects", packs=tmp_path / "packs")
    kidney = store.get("kidney_section")
    assert kidney is not None and kidney.origin == "library" and len(kidney.art.shapes) == 3  # whole
    listed = next(o for o in store.approved() if o.id == "kidney_section")
    assert is_thin(listed) and not is_thin(kidney)  # lists and matching keep it light
    assert listed.aspect == kidney.aspect and listed.aliases == kidney.aliases
    assert with_art(listed).art == kidney.art and with_art(kidney) is kidney
    assert len(store.all()) > 1  # beside the built-in library
    store.save(kidney.model_copy(update={"status": "retired", "aliases": ["kidney", "kidneys"]}))
    again = store.get("kidney_section")
    assert again is not None and again.status == "retired" and again.aliases == ["kidney", "kidneys"]
    assert again not in store.approved()
    with pytest.raises(ValueError):
        store.delete("kidney_section")
    reset = store.reset("kidney_section")
    assert reset is not None and reset.status == "approved"
    # A pack drawing never takes a built-in drawing's id.
    write_pack(tmp_path / "packs", [drawing("antibody", "CC BY 3.0").model_dump()])
    clash = ObjectStore(tmp_path / "objects2", packs=tmp_path / "packs")
    assert clash.get("antibody") is not None and clash.get("antibody").source.pack is None  # type: ignore[union-attr]
    assert any("already a built-in drawing" in e for e in clash.errors)
    # No packs: the library alone.
    assert ObjectStore(tmp_path / "objects3", packs=None).get("kidney_section") is None


def test_only_drawings_whose_licence_asks_are_credited(tmp_path: Path) -> None:
    objs = [
        drawing("kidney_section", "CC BY 3.0"),
        drawing("liver", "CC BY 3.0"),
        drawing("kidney_section", "CC BY 3.0"),  # the same drawing twice: once
        drawing("frog", "CC0 1.0", author="James Lloyd"),
        drawing("heart", None),  # the product's own
    ]
    got = credits(objs)
    assert got == [
        {
            "author": "Servier",
            "pack": "bioicons",
            "license": "CC BY 3.0",
            "license_url": "https://creativecommons.org/licenses/by/3.0/",
            "drawings": [
                {"title": "Kidney section", "url": "https://example.org/kidney_section.svg"},
                {"title": "Liver", "url": "https://example.org/liver.svg"},
            ],
        }
    ]
    assert objs[0].source.credit == "Servier, CC BY 3.0" and objs[3].source.credit is None


def test_a_lessons_credits_gather_its_scenes(tmp_path: Path) -> None:
    gen = Generation(request=GenerationRequest(topic="Kidneys"))
    brief = StoryboardScene.model_validate(
        {
            "title": "t",
            "purpose": "p",
            "duration_s": 10,
            "layout": "stage_with_caption",
            "transition": "fade",
            "visual_plan": "v",
            "objects_new": [],
            "objects_kept": [],
            "objects_removed": [],
            "physics": None,
            "beats": [{"narration": "n", "on_screen": "o"}],
            "engagement": "e",
            "checkpoint": None,
        }
    )
    for i, names in enumerate([["kidney_section"], ["kidney_section", "liver"], []]):
        rec = SceneRecord(index=i, id=f"s{i}", title="t", chapter_id="c1", brief=brief)
        found = credits(drawing(n, "CC BY 3.0") for n in names)
        rec.saved = {"used": names, **({"credits": found} if found else {})}
        gen.scenes.append(rec)
    out = gen.summary()["credits"]
    assert len(out) == 1 and [d["title"] for d in out[0]["drawings"]] == ["Kidney section", "Liver"]
    assert Generation(request=GenerationRequest(topic="None")).summary()["credits"] == []
