"""The built-in library (ADR-055, Plan 1): drawings made for the product.

Each object is authored as two files in `library/src/`: `<id>.svg` (the drawing, with `data-part`
names and `data-fill`/`data-stroke` color roles) and `<id>.json` (what it is: names, context words,
part labels, size). `build` turns them into `library/<id>.json`, the form lessons use: fitted art,
an anchor on every part, and the quality checks, which a built-in drawing must all pass. The built
files are committed; `check` confirms they match their sources (CI).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from animator.objects.models import Quality, SavedObject, Source
from animator.objects.quality import check, to_art, with_anchors
from animator.objects.store import LIBRARY_DIR
from animator.objects.svg import parse_svg

SOURCES = LIBRARY_DIR / "src"
# Built-in objects carry fixed times, so a build is the same on every machine (2026-10-09).
EPOCH = 1_791_504_000.0
_META_KEYS = {
    "id",
    "version",
    "title",
    "concept",
    "description",
    "view",
    "aliases",
    "context",
    "not_for",
    "prefer",
    "subjects",
    "parts",
    "size",
}


@dataclass
class Built:
    obj: SavedObject | None
    problems: list[str]
    warnings: list[str]


def build_object(meta: dict[str, Any], svg_text: str, file: str) -> Built:
    unknown = set(meta) - _META_KEYS
    if unknown:
        return Built(None, [f"unknown fields: {', '.join(sorted(unknown))}"], [])
    try:
        imported = parse_svg(svg_text)
        art, found = with_anchors(to_art(imported.shapes, imported.anchors))
        obj = SavedObject.model_validate(
            {
                **meta,
                "art": art.model_dump(),
                "status": "approved",
                "origin": "library",
                "source": Source(kind="library", file=file).model_dump(),
                "created_at": EPOCH,
                "updated_at": EPOCH,
            }
        )
    except ValueError as e:
        return Built(None, [str(e)], [])
    quality = check(obj, found)
    quality = Quality(score=quality.score, checks=quality.checks, checked_at=EPOCH)
    obj = obj.model_copy(update={"quality": quality})
    problems = [f"{c.name}: {c.detail}" for c in quality.checks if not c.ok]
    # A phrase that means something else hides the drawing from any scene that says it: one word on its
    # own ("idea") would hide the bulb from every scene with "the key idea" in it.
    problems += [
        f"not_for: '{p}' is one word; use the phrase that means something else (e.g. 'bright idea')"
        for p in obj.not_for
        if len(p.replace("-", " ").split()) < 2
    ]
    return Built(obj, problems, imported.warnings)


def build(src: Path = SOURCES) -> dict[str, Built]:
    out: dict[str, Built] = {}
    for meta_path in sorted(src.glob("*.json")):
        svg_path = meta_path.with_suffix(".svg")
        if not svg_path.exists():
            out[meta_path.stem] = Built(None, [f"no drawing {svg_path.name}"], [])
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("id") != meta_path.stem:
            out[meta_path.stem] = Built(None, [f"its id is not {meta_path.stem}"], [])
            continue
        out[meta_path.stem] = build_object(meta, svg_path.read_text(encoding="utf-8"), f"src/{svg_path.name}")
    return out


def serialized(obj: SavedObject) -> str:
    return obj.model_dump_json(indent=1, exclude_none=True) + "\n"


def write(built: dict[str, Built], out_dir: Path = LIBRARY_DIR) -> list[str]:
    """Write the objects that passed every check; the names of the ones that did not."""
    failed = []
    for oid, b in built.items():
        if b.obj is None or b.problems:
            failed.append(oid)
            continue
        (out_dir / f"{oid}.json").write_text(serialized(b.obj), encoding="utf-8", newline="\n")
    keep = {f"{oid}.json" for oid in built}
    for stale in out_dir.glob("*.json"):
        if stale.name not in keep:
            stale.unlink()
    return failed


def drift(built: dict[str, Built], out_dir: Path = LIBRARY_DIR) -> list[str]:
    """Built-in objects whose committed file differs from a fresh build of its sources."""
    out = []
    for oid, b in built.items():
        path = out_dir / f"{oid}.json"
        if b.obj is None or not path.exists() or path.read_text(encoding="utf-8") != serialized(b.obj):
            out.append(oid)
    names = {f"{oid}.json" for oid in built}
    out += [p.stem for p in out_dir.glob("*.json") if p.name not in names]
    return out
