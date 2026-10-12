"""/api/v1/admin/objects: the saved drawings cost-saver lessons place (ADR-055).

- Reading (`objects:read`): every drawing, built-in and in the warehouse, with its status, where it
  came from, its checks' score and how many scenes placed it; one drawing whole (its art, checks,
  names and words, and what to watch out for); the harvest's progress; and which drawings a scene
  brief would be offered (to try the matching before approving).
- Curating (`objects:curate`): approve, reject, retire or restore a drawing; change its names, the
  words it is used for, its part labels and sizes (never the drawing); import an SVG drawing; harvest
  finished lessons now; delete a warehouse drawing.

A drawing reaches lessons only once approved. Approving one that is not built-in needs context words
(the words that show a lesson is about the thing) and every required check passed, so a harvested
"signal" is never offered to a lesson about nerve signals.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from animator.objects.harvest import REQUIRED, Harvester
from animator.objects.matching import SceneText, offers_for
from animator.objects.models import SavedObject, Source
from animator.objects.quality import check, to_art, with_anchors
from animator.objects.service import ObjectLibrary
from animator.objects.svg import parse_svg
from animator.platform.deps import ObjectsCurator, ObjectsReader, Plat, client_ip
from animator.platform.schemas import HarvestIn, ObjectEditIn, ObjectImportIn, ObjectMatchIn, ObjectReviewIn

router = APIRouter(prefix="/api/v1/admin/objects", tags=["admin: saved objects"])

View = Literal["all", "approved", "candidate", "rejected", "retired", "library"]
Origin = Literal["all", "library", "harvest", "import"]
Sort = Literal["score", "new", "name", "uses"]
_ENTRY = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.+)$")


def _library(request: Request) -> ObjectLibrary:
    lib = getattr(request.app.state, "objects", None)
    if not isinstance(lib, ObjectLibrary):
        raise HTTPException(503, "Saved objects are turned off on this server (ANIMATOR_OBJECTS_ENABLED).")
    return lib


def _get(lib: ObjectLibrary, obj_id: str) -> SavedObject:
    obj = lib.store.get(obj_id)
    if obj is None:
        raise HTTPException(404, "No saved drawing with this id.")
    return obj


def cautions(obj: SavedObject, others: list[SavedObject]) -> list[str]:
    """What an admin should know before approving (or while it is in use)."""
    out: list[str] = []
    if not obj.context:
        out.append("No context words: it would be offered wherever one of its names appears.")
    out += [
        f"'{p}' (never for) is one word: it would hide the drawing from every scene that says it. "
        "Use the phrase that means something else."
        for p in obj.not_for
        if len(p.replace("-", " ").split()) < 2
    ]
    names = " ".join(a.lower() for a in obj.aliases)
    out += [
        f"Context word '{c}' is part of its names: names never count as context. Add a word that shows "
        "the lesson is about it (for a traffic light: road, car, traffic)."
        for c in obj.context
        if c.lower() in names
    ]
    mine = {a.lower() for a in obj.aliases}
    for o in others:
        if o.id == obj.id or o.status != "approved" or o.concept.lower() == obj.concept.lower():
            continue
        shared = sorted(mine & {a.lower() for a in o.aliases})
        if shared:
            out.append(f"Shares the name '{shared[0]}' with {o.id} ({o.concept}): both would be offered.")
    if obj.quality is not None:
        out += [f"Check '{c.name}' failed: {c.detail}" for c in obj.quality.checks if not c.ok]
    unnamed = [p for p in obj.art.parts if not obj.parts.get(p)]
    if unnamed:
        out.append(f"Parts without a label: {', '.join(unnamed)}.")
    return out


def not_approvable(obj: SavedObject) -> str | None:
    """Why a drawing cannot be approved (None: it can)."""
    failed = [c.name for c in (obj.quality.checks if obj.quality else []) if not c.ok and c.name in REQUIRED]
    if failed:
        return f"It fails required checks: {', '.join(failed)}."
    if obj.origin != "library" and not obj.context:
        return (
            "Add context words first: the words that show a lesson is about this thing (for a traffic "
            "light: traffic, road, car). Without them it would be offered wherever its name appears."
        )
    return None


def _out(obj: SavedObject, uses: dict[str, int], full: bool = False) -> dict[str, Any]:
    if full:
        return {
            **obj.model_dump(),
            "ref": obj.ref,
            "uses": uses.get(obj.id, 0),
            "anchors": obj.anchor_names(),
        }
    return {
        **obj.summary(),
        "aliases": obj.aliases,
        "uses": uses.get(obj.id, 0),
        "art": obj.art.engine(),
    }


@router.get("")
async def list_objects(
    request: Request,
    p: ObjectsReader,
    view: View = "all",
    origin: Origin = "all",
    q: str | None = None,
    sort: Sort = "score",
    offset: int = 0,
    limit: int = 48,
) -> dict[str, Any]:
    lib = _library(request)
    every = await asyncio.to_thread(lib.store.all)
    uses = await asyncio.to_thread(lib.store.use_counts)
    counts = {k: 0 for k in ("approved", "candidate", "rejected", "retired")}
    for o in every:
        counts[o.status] += 1
    items = every
    if view == "library":
        items = [o for o in items if o.origin == "library"]
    elif view != "all":
        items = [o for o in items if o.status == view]
    if origin != "all":
        items = [o for o in items if o.origin == origin]
    if q and q.strip():
        needle = q.strip().lower()
        items = [
            o
            for o in items
            if needle in o.id
            or needle in o.title.lower()
            or needle in o.concept.lower()
            or any(needle in a.lower() for a in o.aliases)
        ]
    key = {
        "score": lambda o: (-(o.quality.score if o.quality else 0), o.id),
        "new": lambda o: (-o.updated_at, o.id),
        "name": lambda o: (o.title.lower(), o.id),
        "uses": lambda o: (-uses.get(o.id, 0), o.id),
    }[sort]
    items = sorted(items, key=key)
    limit = max(1, min(limit, 96))
    offset = max(0, offset)
    page = items[offset : offset + limit]
    return {
        "items": [_out(o, uses) for o in page],
        "total": len(items),
        "has_more": offset + limit < len(items),
        "counts": counts,
        "library": sum(1 for o in every if o.origin == "library"),
    }


@router.get("/stats")
async def stats(request: Request, p: ObjectsReader) -> dict[str, Any]:
    lib = _library(request)
    every = await asyncio.to_thread(lib.store.all)
    state = await asyncio.to_thread(lib.store.read_state)
    uses = await asyncio.to_thread(lib.store.use_counts)
    harvester = getattr(request.app.state, "harvester", None)
    return {
        "by_status": {
            s: sum(1 for o in every if o.status == s)
            for s in ("approved", "candidate", "rejected", "retired")
        },
        "by_origin": {s: sum(1 for o in every if o.origin == s) for s in ("library", "harvest", "import")},
        "placed": sum(uses.values()),
        "harvest": {
            "background": bool(harvester is not None and getattr(harvester, "_task", None) is not None),
            "lessons_read": len(state.get("seen") or {}),
            "turned_down": len(state.get("rejected") or []),
            "last_run": state.get("last_run"),
            "last": state.get("last"),
        },
        "errors": lib.store.errors,
    }


@router.post("/match")
async def try_matching(request: Request, body: ObjectMatchIn, p: ObjectsReader) -> dict[str, Any]:
    """Which drawings a scene with this brief would be offered (approved ones), and which drawings
    waiting for review would also match."""
    lib = _library(request)
    objects: list[str] = []
    rest: list[str] = []
    for line in body.brief.splitlines():
        (objects if _ENTRY.match(line) else rest).append(line)
    scene = SceneText(objects=objects, body=rest, lesson=[body.lesson] if body.lesson else [])
    every = await asyncio.to_thread(lib.store.all)
    limit = lib.settings.animator_objects_max_offers
    offered = offers_for(scene, [o for o in every if o.status == "approved"], limit)
    waiting = offers_for(scene, [o for o in every if o.status == "candidate"], limit)
    return {
        "offered": [
            {"id": o.obj.id, "title": o.obj.title, "for": o.for_ids, "mentions": o.mentions} for o in offered
        ],
        "candidates": [{"id": o.obj.id, "title": o.obj.title, "for": o.for_ids} for o in waiting],
    }


@router.get("/{obj_id}")
async def get_object(request: Request, obj_id: str, p: ObjectsReader) -> dict[str, Any]:
    lib = _library(request)
    obj = _get(lib, obj_id)
    uses = await asyncio.to_thread(lib.store.use_counts)
    others = await asyncio.to_thread(lib.store.all)
    return {
        **_out(obj, uses, full=True),
        "cautions": cautions(obj, others),
        "approvable": not_approvable(obj) is None,
        "why_not": not_approvable(obj),
    }


async def _save(lib: ObjectLibrary, obj: SavedObject) -> SavedObject:
    try:
        return await asyncio.to_thread(lib.store.save, obj)
    except ValidationError as e:
        first = e.errors()[0]
        raise HTTPException(422, f"{'.'.join(str(x) for x in first['loc'])}: {first['msg']}") from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.patch("/{obj_id}")
async def edit_object(
    request: Request, obj_id: str, body: ObjectEditIn, p: ObjectsCurator, platform: Plat
) -> dict[str, Any]:
    lib = _library(request)
    obj = _get(lib, obj_id)
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(422, "Nothing to change.")
    try:
        edited = SavedObject.model_validate({**obj.model_dump(), **changes, "updated_at": time.time()})
    except ValidationError as e:
        first = e.errors()[0]
        raise HTTPException(
            422, f"{'.'.join(str(x) for x in first['loc']) or 'drawing'}: {first['msg']}"
        ) from e
    # The checks (named parts, mostly) follow the new words.
    edited = edited.model_copy(update={"quality": check(edited)})
    saved = await _save(lib, edited)
    await platform.audit("objects.edit", p.user_id, obj_id, client_ip(request), fields=sorted(changes))
    return await get_object(request, saved.id, p)


@router.post("/{obj_id}/review")
async def review_object(
    request: Request, obj_id: str, body: ObjectReviewIn, p: ObjectsCurator, platform: Plat
) -> dict[str, Any]:
    lib = _library(request)
    obj = _get(lib, obj_id)
    if body.decision == "approve":
        obj = obj.model_copy(update={"quality": check(obj)}) if obj.origin != "library" else obj
        why = not_approvable(obj)
        if why:
            raise HTTPException(422, why)
        status = "approved"
    elif body.decision == "reject":
        if obj.origin == "library":
            raise HTTPException(422, "A built-in drawing is retired, not rejected.")
        status = "rejected"
    elif body.decision == "retire":
        status = "retired"
    else:  # restore
        status = "approved" if obj.origin == "library" else "candidate"
    now = time.time()
    updated = obj.model_copy(
        update={
            "status": status,
            "note": body.note or obj.note,
            "reviewed_at": now,
            "reviewed_by": p.user_id,
            "updated_at": now,
        }
    )
    await _save(lib, updated)
    await platform.audit(f"objects.{body.decision}", p.user_id, obj_id, client_ip(request), title=obj.title)
    return await get_object(request, obj_id, p)


@router.post("/{obj_id}/reset")
async def reset_object(request: Request, obj_id: str, p: ObjectsCurator, platform: Plat) -> dict[str, Any]:
    """A built-in drawing back to how it ships (its edits and decisions undone)."""
    lib = _library(request)
    obj = _get(lib, obj_id)
    if obj.origin != "library":
        raise HTTPException(422, "Only a built-in drawing can be reset.")
    await asyncio.to_thread(lib.store.reset, obj_id)
    await platform.audit("objects.reset", p.user_id, obj_id, client_ip(request))
    return await get_object(request, obj_id, p)


@router.delete("/{obj_id}")
async def delete_object(request: Request, obj_id: str, p: ObjectsCurator, platform: Plat) -> dict[str, Any]:
    """Delete a warehouse drawing. Lessons that placed it keep it (they carry the drawing)."""
    lib = _library(request)
    obj = _get(lib, obj_id)
    if obj.origin == "library":
        raise HTTPException(422, "A built-in drawing cannot be deleted; retire it instead.")
    await asyncio.to_thread(lib.store.delete, obj_id)
    await platform.audit("objects.delete", p.user_id, obj_id, client_ip(request), title=obj.title)
    return {"ok": True}


@router.post("/import")
async def import_svg(
    request: Request, body: ObjectImportIn, p: ObjectsCurator, platform: Plat
) -> dict[str, Any]:
    """An SVG drawing into the warehouse, as a candidate: shapes only (text, images and scripts are
    left out), fitted, its parts given anchors, checked. It is used once approved."""
    lib = _library(request)
    try:
        imported = await asyncio.to_thread(parse_svg, body.svg)
        art, spots = await asyncio.to_thread(lambda: with_anchors(to_art(imported.shapes, imported.anchors)))
    except ValueError as e:
        raise HTTPException(422, f"This SVG cannot be used: {e}") from e
    fingerprint = art.fingerprint()
    same = next((o for o in await asyncio.to_thread(lib.store.all) if o.fingerprint == fingerprint), None)
    if same is not None:
        raise HTTPException(409, f"This drawing is already saved as '{same.id}' ({same.title}).")
    slug = re.sub(r"[^a-z0-9]+", "_", body.concept.lower()).strip("_")[:48] or "drawing"
    try:
        obj = SavedObject(
            id=f"{slug}_{fingerprint[:6]}" if slug[0].isalpha() else f"d_{slug}_{fingerprint[:6]}",
            title=body.title,
            concept=body.concept,
            description=body.description,
            aliases=body.aliases or [body.concept],
            parts={part: part.replace("_", " ") for part in art.parts},
            art=art,
            status="candidate",
            origin="import",
            source=Source(kind="import", file=body.file or None, by=p.user_id),
            fingerprint=fingerprint,
        )
    except ValidationError as e:
        first = e.errors()[0]
        raise HTTPException(422, f"{'.'.join(str(x) for x in first['loc'])}: {first['msg']}") from e
    obj = obj.model_copy(update={"quality": check(obj, spots)})
    await _save(lib, obj)
    await platform.audit("objects.import", p.user_id, obj.id, client_ip(request), title=obj.title)
    return {**(await get_object(request, obj.id, p)), "import_warnings": imported.warnings}


@router.post("/harvest")
async def harvest_now(request: Request, body: HarvestIn, p: ObjectsCurator, platform: Plat) -> dict[str, Any]:
    """Read finished lessons not read before, now (it otherwise runs in the background)."""
    _library(request)
    harvester = getattr(request.app.state, "harvester", None)
    if not isinstance(harvester, Harvester):
        raise HTTPException(503, "The harvest is not available on this server.")
    report = await harvester.run(limit=body.lessons, force=True)
    await platform.audit(
        "objects.harvest", p.user_id, "", client_ip(request), lessons=report.lessons, added=len(report.added)
    )
    return {
        "lessons": report.lessons,
        "scenes": report.scenes,
        "added": report.added,
        "turned_down": report.turned_down,
        "seconds": report.seconds,
    }
