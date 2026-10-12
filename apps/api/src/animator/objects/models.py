"""Saved objects (ADR-055): a drawing, what it is, and when a lesson may use it."""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
# The engine's box anchors: never part names (they would hide `heart.top`).
RESERVED = {
    "center",
    "top",
    "bottom",
    "left",
    "right",
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
}

# The engine's color roles (contracts/catalog.json `colorRoles`; a test keeps them equal).
COLOR_ROLES = (
    "background", "surface", "surface_alt", "text", "muted", "accent", "highlight", "object_a",
    "object_b", "object_c", "fluid", "glass", "grid", "shadow", "force_gravity", "force_buoyant",
    "force_normal", "force_applied", "velocity", "pressure", "positive", "negative", "red", "orange",
    "yellow", "green", "blue", "sky", "purple", "pink", "brown", "gray",
)  # fmt: skip
COLOR_VARIANTS = ("light", "dark", "soft")
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_ROLES = set(COLOR_ROLES)

Status = Literal["approved", "candidate", "rejected", "retired"]
Origin = Literal["library", "harvest", "import"]


def paint_ok(v: str | None) -> bool:
    """`none`, a hex color, or a color role with an optional variant (`red`, `red.dark`)."""
    if v is None or v == "none" or _HEX.match(v):
        return True
    role, _, variant = v.partition(".")
    return role in _ROLES and (not variant or variant in COLOR_VARIANTS)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Shape(_Strict):
    d: str = Field(min_length=1, max_length=20_000)
    fill: str | None = None
    stroke: str | None = None
    strokeWidth: float | None = Field(default=None, ge=0, le=200)  # the engine's names
    opacity: float | None = Field(default=None, ge=0, le=1)
    fillRule: Literal["nonzero", "evenodd"] | None = None
    part: str | None = None

    @field_validator("fill", "stroke")
    @classmethod
    def _paint(cls, v: str | None) -> str | None:
        if not paint_ok(v):
            raise ValueError(f"unknown color '{v}'")
        return v

    @field_validator("part")
    @classmethod
    def _part(cls, v: str | None) -> str | None:
        if v is not None and (not NAME_RE.match(v) or v in RESERVED):
            raise ValueError(f"part name '{v}' must be snake_case and not a box anchor")
        return v


class Anchor(_Strict):
    name: str
    x: float
    y: float

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v) or v in RESERVED:
            raise ValueError(f"anchor name '{v}' must be snake_case and not a box anchor")
        return v


class Art(_Strict):
    """The drawing: shapes in its own coordinate box (`w` x `h`), painted in order."""

    w: float = Field(gt=0, le=4000)
    h: float = Field(gt=0, le=4000)
    shapes: list[Shape] = Field(min_length=1, max_length=400)
    anchors: list[Anchor] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def _limits(self) -> Art:
        if sum(len(s.d) for s in self.shapes) > 120_000:
            raise ValueError("the drawing is too large (more than 120,000 characters of path data)")
        names = [a.name for a in self.anchors]
        if len(names) != len(set(names)):
            raise ValueError("anchor names repeat")
        return self

    @property
    def parts(self) -> list[str]:
        seen: dict[str, None] = {}
        for s in self.shapes:
            if s.part:
                seen.setdefault(s.part, None)
        return list(seen)

    def engine(self) -> dict[str, Any]:
        """The art as the engine's `figure` takes it (no empty fields)."""
        return self.model_dump(exclude_none=True, exclude_defaults=False)

    def fingerprint(self) -> str:
        """Same drawing, same fingerprint (shapes and their paint; not names or anchors)."""
        body = [[s.d, s.fill, s.stroke, s.strokeWidth, s.opacity, s.fillRule] for s in self.shapes]
        raw = json.dumps([round(self.w, 1), round(self.h, 1), body], separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


class Size(_Strict):
    """Width on the 1600 x 900 canvas, in px: the least that stays legible, the usual, the most."""

    min: float = Field(default=160, ge=24, le=1500)
    default: float = Field(default=320, ge=24, le=1500)
    max: float = Field(default=640, ge=24, le=1500)

    @model_validator(mode="after")
    def _order(self) -> Size:
        if not self.min <= self.default <= self.max:
            raise ValueError("size must have min <= default <= max")
        return self


class Check(_Strict):
    name: str
    ok: bool
    detail: str = ""


class Quality(_Strict):
    score: float = Field(ge=0, le=1)
    checks: list[Check] = Field(default_factory=list)
    checked_at: float = Field(default_factory=time.time)

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks)


class Source(_Strict):
    kind: Origin
    generation_id: str | None = None
    scene_id: str | None = None
    object_id: str | None = None
    lesson_title: str | None = None
    file: str | None = None
    by: str | None = None


def _phrases(v: list[str], limit: int, what: str) -> list[str]:
    out: list[str] = []
    for p in v:
        p = " ".join(str(p).split()).strip()
        if not p:
            continue
        if len(p) > 60:
            raise ValueError(f"{what} '{p[:20]}...' is longer than 60 characters")
        if p.lower() not in (x.lower() for x in out):
            out.append(p)
    if len(out) > limit:
        raise ValueError(f"at most {limit} {what}")
    return out


class SavedObject(_Strict):
    id: str
    version: int = Field(default=1, ge=1)
    title: str = Field(min_length=1, max_length=80)
    # What it is, in plain words ("human heart"): the thing a scene must be showing to use it.
    concept: str = Field(min_length=1, max_length=80)
    # What the drawing shows, for the scene writer ("the heart seen from the front: ...").
    description: str = Field(default="", max_length=300)
    view: str = Field(default="", max_length=40)
    # Names a scene brief may use for it; a match is a whole word or phrase.
    aliases: list[str] = Field(min_length=1)
    # Word stems of which one must appear in the scene or lesson for a match ("blood", "pump").
    context: list[str] = Field(default_factory=list)
    # Phrases that mean something else ("heart shape", "heart of").
    not_for: list[str] = Field(default_factory=list)
    # Word stems that make this one the better choice among drawings of the same thing.
    prefer: list[str] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)
    # Part name -> how to say it ("left_ventricle": "left ventricle").
    parts: dict[str, str] = Field(default_factory=dict)
    size: Size = Field(default_factory=Size)
    art: Art
    status: Status = "candidate"
    origin: Origin = "library"
    source: Source = Field(default_factory=lambda: Source(kind="library"))
    quality: Quality | None = None
    fingerprint: str = ""
    note: str = Field(default="", max_length=500)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    reviewed_at: float | None = None
    reviewed_by: str | None = None

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError("id must be snake_case, 2 to 64 characters")
        return v

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, v: list[str]) -> list[str]:
        out = _phrases(v, 24, "aliases")
        if not out:
            raise ValueError("at least one alias")
        return out

    @field_validator("context")
    @classmethod
    def _context(cls, v: list[str]) -> list[str]:
        return _phrases(v, 60, "context words")

    @field_validator("not_for")
    @classmethod
    def _not_for(cls, v: list[str]) -> list[str]:
        return _phrases(v, 40, "not-for phrases")

    @field_validator("prefer")
    @classmethod
    def _prefer(cls, v: list[str]) -> list[str]:
        return _phrases(v, 40, "preferred words")

    @model_validator(mode="after")
    def _consistent(self) -> SavedObject:
        known = set(self.art.parts) | {a.name for a in self.art.anchors}
        unknown = [p for p in self.parts if p not in known]
        if unknown:
            raise ValueError(f"labels for parts the drawing does not have: {', '.join(unknown)}")
        if not self.fingerprint:
            self.fingerprint = self.art.fingerprint()
        return self

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def aspect(self) -> float:
        """Height per unit of width."""
        return self.art.h / self.art.w

    def anchor_names(self) -> list[str]:
        names = list(self.art.parts)
        names += [a.name for a in self.art.anchors if a.name not in names]
        return names

    def summary(self) -> dict[str, Any]:
        """Everything but the art (lists)."""
        return {
            "id": self.id,
            "version": self.version,
            "title": self.title,
            "concept": self.concept,
            "view": self.view,
            "status": self.status,
            "origin": self.origin,
            "score": self.quality.score if self.quality else None,
            "passed": self.quality.passed if self.quality else None,
            "parts": len(self.art.parts),
            "shapes": len(self.art.shapes),
            "aspect": round(self.aspect, 3),
            "subjects": self.subjects,
            "source": self.source.model_dump(exclude_none=True),
            "updated_at": self.updated_at,
            "reviewed_at": self.reviewed_at,
        }


# Fields an admin may change on any object (built-in ones keep their drawing).
EDITABLE = (
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
    "note",
)
