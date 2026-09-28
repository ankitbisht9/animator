"""Export service: planning, ordered parallel rendering, segments, caching, retries,
failure reporting, audio hook and bundles. Uses a fake renderer (synthetic PNG frames)
and the real ffmpeg; the browser path is covered in test_export_e2e.py."""

from __future__ import annotations

import asyncio
import io
import json
import math
import random
import struct
import wave
from collections.abc import Callable, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from animator.export.exporter import (
    Exporter,
    ExportError,
    FileAudio,
    load_bundle,
    make_bundle,
    render_frames,
)
from animator.export.ffmpeg import find_ffmpeg, find_ffprobe, mp4_top_level_boxes, probe
from animator.export.plan import (
    ExportSettings,
    Segment,
    frame_at,
    parse_scene_selection,
    plan_segments,
    segment_key,
    total_frames,
)

FPS = 10
SETTINGS = ExportSettings(
    width=64, height=64, fps=FPS, crf=23, preset="ultrafast", workers=3, segment_retries=2
)


# ---------------------------------------------------------------------- fakes


def png_for(scene: int, t: float, size: int = 64) -> bytes:
    shade = int(t * 40) % 256
    img = Image.new("RGB", (size, size), (shade, 60 * scene % 256, 255 - shade))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeRenderer:
    def __init__(self, pool: FakePool) -> None:
        self.pool = pool

    async def at_png(self, scene_index: int, local_t: float) -> bytes:
        self.pool.calls += 1
        await asyncio.sleep(random.random() / 500)  # finish out of order
        if self.pool.fail is not None:
            self.pool.fail(scene_index, local_t, self.pool)
        return png_for(scene_index, local_t)


class FakePool:
    build_id = "fake-build"
    browser_version = "fake-browser"

    def __init__(self, scenes: list[dict[str, Any]], size: int = 3) -> None:
        self.scenes = scenes
        self._renderers = [FakeRenderer(self) for _ in range(size)]
        self.restarts = 0
        self.calls = 0
        self.fail: Callable[[int, float, FakePool], None] | None = None
        self.invalid: set[str] = set()

    @property
    def renderers(self) -> Sequence[FakeRenderer]:
        return self._renderers

    async def load(self, lesson: dict[str, Any]) -> dict[str, Any]:
        start = 0.0
        out = []
        for sc in lesson["scenes"]:
            ok = sc["id"] not in self.invalid
            errors = [] if ok else [{"message": "unknown component 'nope'"}]
            out.append(
                {
                    "id": sc["id"],
                    "title": sc["id"],
                    "start": start,
                    "duration": sc["duration"],
                    "ok": ok,
                    "errors": errors,
                    "themeId": "educational_clean",
                }
            )
            start += sc["duration"]
        return {
            "title": lesson["title"],
            "engineVersion": "0.1.0",
            "dslVersion": "1.0",
            "duration": start,
            "scenes": out,
        }

    async def restart(self) -> None:
        self.restarts += 1


LESSON: dict[str, Any] = {
    "title": "Fake",
    "scenes": [{"id": "s1", "duration": 1.0}, {"id": "s2", "duration": 1.5}, {"id": "s3", "duration": 0.8}],
}


@pytest.fixture
def ffmpeg() -> str:
    try:
        return find_ffmpeg()
    except Exception as e:  # pragma: no cover - environment problem, not a test result
        pytest.fail(f"ffmpeg is required for export tests: {e}")


def exporter(ffmpeg: str, settings: ExportSettings = SETTINGS) -> Exporter:
    return Exporter(settings, ffmpeg, log=lambda _: None, frame_timeout_s=10)


# ---------------------------------------------------------------------- planning


def test_segments_tile_the_timeline_exactly() -> None:
    scenes = [
        {"id": "a", "start": 0.0, "duration": 21.0},
        {"id": "b", "start": 21.0, "duration": 24.5},
        {"id": "c", "start": 45.5, "duration": 0.35},
        {"id": "d", "start": 45.85, "duration": 7.0},
    ]
    segs = plan_segments(scenes, 30)
    assert segs[0].first_frame == 0
    assert all(a.end_frame == b.first_frame for a, b in pairwise(segs))
    assert segs[-1].end_frame == total_frames(52.85, 30)
    assert sum(s.frame_count for s in segs) == total_frames(52.85, 30)


def test_frame_rounding_and_local_time() -> None:
    assert frame_at(24.5, 30) == 735
    assert frame_at(0.35 * 3, 30) == 32  # 31.5 rounds half up, despite float noise
    seg = Segment(1, "b", "", 10.0, 2.0, 300, 60)
    assert seg.local_time(300, 30) == 0.0
    assert seg.local_time(359, 30) == pytest.approx(59 / 30)
    assert seg.local_time(299, 30) == 0.0  # clamped into the scene


def test_scene_shorter_than_a_frame_is_rejected() -> None:
    with pytest.raises(ValueError, match="shorter than one frame"):
        plan_segments([{"id": "blip", "start": 0.0, "duration": 0.01}], 30)


def test_scene_selection() -> None:
    assert parse_scene_selection("1,3-4", 5) == [0, 2, 3]
    for bad in ("0", "2-9", ""):
        with pytest.raises(ValueError):
            parse_scene_selection(bad, 5)


def test_settings_validation() -> None:
    with pytest.raises(ValueError, match="even"):
        ExportSettings(width=641, height=360)
    with pytest.raises(ValueError, match="mp4"):
        ExportSettings(formats=("webm",))
    with pytest.raises(ValueError, match="preset"):
        ExportSettings(preset="warp")
    assert "workers" not in ExportSettings().render_key()


def test_segment_key_depends_on_earlier_scenes_only() -> None:
    scenes = [{"id": "a", "x": 1}, {"id": "b", "x": 2}, {"id": "c", "x": 3}]
    base = segment_key(scenes, 1, SETTINGS, "build", "0.1.0")
    later = segment_key([*scenes[:2], {"id": "c", "x": 99}], 1, SETTINGS, "build", "0.1.0")
    earlier = segment_key([{"id": "a", "x": 0}, *scenes[1:]], 1, SETTINGS, "build", "0.1.0")
    assert base == later
    assert base != earlier
    assert base != segment_key(scenes, 1, SETTINGS, "other-build", "0.1.0")
    assert base != segment_key(scenes, 1, ExportSettings(width=66, height=64, fps=FPS), "build", "0.1.0")


# ---------------------------------------------------------------------- ordered rendering


async def test_frames_reach_the_encoder_in_order_whatever_order_they_finish() -> None:
    pool = FakePool(LESSON["scenes"], size=4)
    seg = Segment(1, "s2", "", 1.0, 1.5, 10, 15)
    got: list[bytes] = []

    async def sink(png: bytes) -> None:
        got.append(png)

    hashes = await render_frames(pool.renderers, seg, FPS, sink, 5)
    assert got == [png_for(1, seg.local_time(f, FPS)) for f in range(10, 25)]
    assert len(hashes) == 15


async def test_a_renderer_error_stops_the_segment() -> None:
    pool = FakePool(LESSON["scenes"])

    def boom(scene: int, t: float, p: FakePool) -> None:
        if t > 0.5:
            raise RuntimeError("browser crashed")

    pool.fail = boom
    seg = Segment(1, "s2", "", 1.0, 1.5, 10, 15)

    async def sink(png: bytes) -> None:
        pass

    with pytest.raises(ExceptionGroup):
        await render_frames(pool.renderers, seg, FPS, sink, 5)


# ---------------------------------------------------------------------- exporter


async def test_exports_segments_and_a_verified_mp4(ffmpeg: str, tmp_path: Path) -> None:
    pool = FakePool(LESSON["scenes"])
    res = await exporter(ffmpeg).export(pool, LESSON, tmp_path, "fake")
    assert res.complete
    mp4 = res.outputs["mp4"]
    p = probe(find_ffprobe(ffmpeg), mp4)
    assert p.frames == res.total_frames == 33
    assert (p.codec, p.pix_fmt, p.width, p.height) == ("h264", "yuv420p", 64, 64)
    assert p.duration == pytest.approx(3.3, abs=1 / FPS)
    assert p.color_space == "bt709"
    boxes = mp4_top_level_boxes(mp4)
    assert boxes.index("moov") < boxes.index("mdat"), "faststart"
    assert [s.frames for s in res.segments] == [10, 15, 8]
    assert (tmp_path / "fake" / "segments" / "02-s2.mp4").exists()
    report = json.loads(res.report.read_text(encoding="utf-8"))
    assert report["complete"] and report["totals"]["frames"] == 33
    assert report["outputs"]["mp4"]["probe"]["frames"] == 33


async def test_same_input_gives_identical_frames_and_bytes(ffmpeg: str, tmp_path: Path) -> None:
    a = await exporter(ffmpeg).export(FakePool(LESSON["scenes"], size=1), LESSON, tmp_path / "a", "x")
    b = await exporter(ffmpeg).export(FakePool(LESSON["scenes"], size=4), LESSON, tmp_path / "b", "x")
    assert a.frames_sha256 == b.frames_sha256
    assert a.outputs["mp4"].read_bytes() == b.outputs["mp4"].read_bytes()


async def test_unchanged_segments_are_reused(ffmpeg: str, tmp_path: Path) -> None:
    await exporter(ffmpeg).export(FakePool(LESSON["scenes"]), LESSON, tmp_path, "c")
    pool = FakePool(LESSON["scenes"])
    again = await exporter(ffmpeg).export(pool, LESSON, tmp_path, "c")
    assert all(s.cached for s in again.segments) and pool.calls == 0

    edited = {
        **LESSON,
        "scenes": [LESSON["scenes"][0], {"id": "s2", "duration": 1.5, "edit": 1}, LESSON["scenes"][2]],
    }
    pool = FakePool(edited["scenes"])
    res = await exporter(ffmpeg).export(pool, edited, tmp_path, "c")
    assert [s.cached for s in res.segments] == [True, False, False]  # scene 3 inherits scene 2's end state


async def test_segments_can_be_exported_as_scenes_become_final(ffmpeg: str, tmp_path: Path) -> None:
    part = await exporter(ffmpeg).export(FakePool(LESSON["scenes"]), LESSON, tmp_path, "p", scenes=[0, 1])
    assert not part.complete and part.outputs == {}
    assert [s.index for s in part.segments] == [0, 1]
    pool = FakePool(LESSON["scenes"])
    full = await exporter(ffmpeg).export(pool, LESSON, tmp_path, "p")
    assert full.complete
    assert [s.cached for s in full.segments] == [True, True, False]


async def test_a_failed_segment_is_retried_after_restarting_the_browsers(ffmpeg: str, tmp_path: Path) -> None:
    pool = FakePool(LESSON["scenes"])

    def flaky(scene: int, t: float, p: FakePool) -> None:
        if scene == 1 and p.restarts == 0:
            raise RuntimeError("Target page, context or browser has been closed")

    pool.fail = flaky
    res = await exporter(ffmpeg).export(pool, LESSON, tmp_path, "r")
    assert res.complete
    assert pool.restarts == 1
    assert [s.attempts for s in res.segments] == [1, 2, 1]


async def test_a_segment_that_keeps_failing_reports_its_scene(ffmpeg: str, tmp_path: Path) -> None:
    pool = FakePool(LESSON["scenes"])

    def broken(scene: int, t: float, p: FakePool) -> None:
        if scene == 2:
            raise RuntimeError("render error")

    pool.fail = broken
    with pytest.raises(ExportError) as err:
        await exporter(ffmpeg).export(pool, LESSON, tmp_path, "f")
    assert err.value.scene_index == 2 and err.value.scene_id == "s3"
    assert "scene 3 's3' failed after 3 attempt(s)" in str(err.value)
    assert pool.restarts == 2
    assert not (tmp_path / "f" / "segments" / "03-s3.mp4").exists(), "no partial segment left behind"
    assert (tmp_path / "f" / "segments" / "02-s2.mp4").exists(), "finished scenes are kept"


async def test_scenes_that_failed_validation_are_never_exported(ffmpeg: str, tmp_path: Path) -> None:
    pool = FakePool(LESSON["scenes"])
    pool.invalid = {"s2"}
    with pytest.raises(ExportError, match="scene 2 's2': unknown component 'nope'"):
        await exporter(ffmpeg).export(pool, LESSON, tmp_path, "v")
    assert pool.calls == 0


def write_tone(path: Path, seconds: float) -> None:
    rate = 22050
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(
            b"".join(
                struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
                for i in range(int(rate * seconds))
            )
        )


@pytest.mark.parametrize("audio_seconds", [1.0, 6.0])
async def test_audio_hook_is_padded_or_trimmed_to_the_video(
    ffmpeg: str, tmp_path: Path, audio_seconds: float
) -> None:
    tone = tmp_path / "tone.wav"
    write_tone(tone, audio_seconds)
    res = await exporter(ffmpeg).export(
        FakePool(LESSON["scenes"]), LESSON, tmp_path, "a", audio=FileAudio(tone)
    )
    p = probe(find_ffprobe(ffmpeg), res.outputs["mp4"])
    assert p.has_audio
    assert p.frames == 33
    assert p.duration == pytest.approx(3.3, abs=0.1)


async def test_webm_is_optional_alongside_mp4(ffmpeg: str, tmp_path: Path) -> None:
    s = ExportSettings(width=64, height=64, fps=FPS, crf=30, preset="ultrafast", formats=("mp4", "webm"))
    res = await exporter(ffmpeg, s).export(FakePool(LESSON["scenes"]), LESSON, tmp_path, "w")
    assert set(res.outputs) == {"mp4", "webm"}
    p = probe(find_ffprobe(ffmpeg), res.outputs["webm"])
    assert (p.codec, p.frames) == ("vp9", 33)


async def test_bundle_round_trips_for_re_rendering(ffmpeg: str, tmp_path: Path) -> None:
    res = await exporter(ffmpeg).export(FakePool(LESSON["scenes"]), LESSON, tmp_path, "b")
    lesson = load_bundle(res.bundle)
    assert lesson["scenes"] == LESSON["scenes"] and lesson["title"] == "Fake"
    data = json.loads(res.bundle.read_text(encoding="utf-8"))
    assert data["format"] == "animator.animation" and data["themes"] == ["educational_clean"]
    again = await exporter(ffmpeg).export(FakePool(lesson["scenes"]), lesson, tmp_path / "re", "b")
    assert again.frames_sha256 == res.frames_sha256


def test_bundle_loader_rejects_other_files(tmp_path: Path) -> None:
    bad = tmp_path / "x.animation.json"
    bad.write_text(json.dumps({"format": "something-else"}), encoding="utf-8")
    with pytest.raises(ExportError, match="not an animation bundle"):
        load_bundle(bad)
    summary = {"dslVersion": "1.0", "engineVersion": "0.1.0", "duration": 1, "scenes": []}
    bad.write_text(json.dumps({**make_bundle({"scenes": []}, summary), "version": 99}), encoding="utf-8")
    with pytest.raises(ExportError, match="bundle version"):
        load_bundle(bad)
