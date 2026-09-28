"""Pure export planning: settings, per-scene frame ranges, segment cache keys.

A lesson is exported as one segment per scene. Scene i covers the global frames
[frame(start_i), frame(start_i + duration_i)), so the segments tile the lesson
exactly and their concatenation has the lesson's exact frame count. Each frame
is rendered at a scene-local time, so a segment depends only on its own scene
and the scenes before it (whose end state it inherits), never on later scenes.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from animator.config import Settings

FORMATS = ("mp4", "webm")
PRESETS = (
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
)
# Bumped when the way segments are rendered or encoded changes (invalidates cached segments).
PIPELINE_VERSION = 2


@dataclass(frozen=True)
class ExportSettings:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    crf: int = 18
    preset: str = "medium"
    formats: tuple[str, ...] = ("mp4",)
    workers: int = 4
    segment_retries: int = 2

    def __post_init__(self) -> None:
        if self.width % 2 or self.height % 2:
            raise ValueError(f"width and height must be even for yuv420p, got {self.width}x{self.height}")
        if not (64 <= self.width <= 7680 and 64 <= self.height <= 4320):
            raise ValueError(f"unsupported resolution {self.width}x{self.height}")
        if not 1 <= self.fps <= 120:
            raise ValueError(f"fps must be 1..120, got {self.fps}")
        if not 0 <= self.crf <= 51:
            raise ValueError(f"crf must be 0..51, got {self.crf}")
        if self.preset not in PRESETS:
            raise ValueError(f"unknown x264 preset '{self.preset}'")
        if "mp4" not in self.formats or any(f not in FORMATS for f in self.formats):
            raise ValueError(f"formats must include mp4 and only use {FORMATS}, got {self.formats}")
        if self.workers < 1:
            raise ValueError("workers must be at least 1")
        if self.segment_retries < 0:
            raise ValueError("segment_retries must be >= 0")

    @classmethod
    def from_settings(cls, s: Settings, **overrides: Any) -> ExportSettings:
        base: dict[str, Any] = {
            "width": s.export_width,
            "height": s.export_height,
            "fps": s.export_fps,
            "crf": s.export_crf,
            "workers": s.export_workers,
            "formats": parse_formats(s.export_formats),
        }
        base.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**base)

    def render_key(self) -> dict[str, Any]:
        """Everything that changes the pixels or the encoded bytes (not worker count or retries)."""
        return {
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "crf": self.crf,
            "preset": self.preset,
            "formats": list(self.formats),
        }


def parse_formats(text: str) -> tuple[str, ...]:
    formats = tuple(f.strip().lower() for f in text.split(",") if f.strip())
    return formats or ("mp4",)


def frame_at(t: float, fps: int) -> int:
    """Index of the first frame at or after time t (round half up, tolerant of float noise)."""
    return math.floor(t * fps + 0.5 + 1e-9)


@dataclass(frozen=True)
class Segment:
    index: int
    scene_id: str
    title: str
    start: float
    duration: float
    first_frame: int
    frame_count: int

    @property
    def end_frame(self) -> int:
        return self.first_frame + self.frame_count

    def local_time(self, frame: int, fps: int) -> float:
        """Scene-local time of a global frame, clamped into the scene."""
        return max(0.0, min(self.duration, frame / fps - self.start))

    @property
    def stem(self) -> str:
        return f"{self.index + 1:02d}-{self.scene_id}"


def plan_segments(scenes: Sequence[dict[str, Any]], fps: int) -> list[Segment]:
    """Segments from the engine's scene timeline (`start`, `duration` per scene)."""
    out: list[Segment] = []
    for i, sc in enumerate(scenes):
        start = float(sc["start"])
        duration = float(sc["duration"])
        first = frame_at(start, fps)
        end = frame_at(start + duration, fps)
        if end <= first:
            raise ValueError(f"scene '{sc['id']}' is shorter than one frame at {fps} fps")
        out.append(Segment(i, str(sc["id"]), str(sc.get("title", "")), start, duration, first, end - first))
    for a, b in pairwise(out):
        if a.end_frame != b.first_frame:
            raise ValueError(f"segments '{a.scene_id}' and '{b.scene_id}' do not tile the timeline")
    return out


def total_frames(duration: float, fps: int) -> int:
    return frame_at(duration, fps)


def parse_scene_selection(text: str, count: int) -> list[int]:
    """'1,3-5' (1-based, inclusive) -> [0, 2, 3, 4]."""
    picked: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        lo, _, hi = part.partition("-")
        a = int(lo)
        b = int(hi) if hi else a
        if not (1 <= a <= b <= count):
            raise ValueError(f"scene range '{part}' is outside 1..{count}")
        picked.update(range(a - 1, b))
    if not picked:
        raise ValueError("empty scene selection")
    return sorted(picked)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def segment_key(
    scenes: Sequence[Any], index: int, settings: ExportSettings, build_id: str, engine_version: str
) -> str:
    """Cache key of a segment: its scene, every earlier scene (their end state carries over),
    the render settings, the web build and the engine version."""
    payload = {
        "pipeline": PIPELINE_VERSION,
        "scenes": list(scenes[: index + 1]),
        "index": index,
        "render": settings.render_key(),
        "build": build_id,
        "engine": engine_version,
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def frames_digest(frame_hashes: Sequence[str]) -> str:
    """One fingerprint for an ordered list of frame hashes."""
    h = hashlib.sha256()
    for x in frame_hashes:
        h.update(x.encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()
