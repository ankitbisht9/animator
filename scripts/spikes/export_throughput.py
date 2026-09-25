"""Spike (a): export throughput and determinism with Python Playwright + ffmpeg.

Renders N frames of a ~150-object SVG test page (pure function of t) in headless
Chromium at 1920x1080, with 1..K parallel browser instances each taking a slice
of the frame range. Measures frames/second, checks that two independent renders
produce byte-identical PNGs, and encodes an H.264 MP4 through ffmpeg.

    uv run python scripts/spikes/export_throughput.py [--frames 180]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

from playwright.async_api import Browser, async_playwright

HERE = Path(__file__).resolve().parent
PAGE = (HERE / "spike_page.html").as_uri()
OUT = HERE / "out"

CHROMIUM_ARGS = [
    "--disable-gpu",
    "--force-color-profile=srgb",
    "--font-render-hinting=none",
    "--disable-lcd-text",
    "--hide-scrollbars",
]


async def render_slice(browser: Browser, frames: list[int], out: dict[int, bytes]) -> None:
    page = await browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
    await page.goto(PAGE)
    await page.wait_for_function("window.__ready === true")
    for f in frames:
        await page.evaluate("f => window.__renderFrame(f)", f)
        out[f] = await page.screenshot(type="png")
    await page.close()


async def render(total: int, workers: int) -> tuple[dict[int, bytes], float]:
    async with async_playwright() as pw:
        browsers = [await pw.chromium.launch(args=CHROMIUM_ARGS) for _ in range(workers)]
        out: dict[int, bytes] = {}
        # Contiguous slices: each browser renders its own range, like per-segment export.
        size = -(-total // workers)
        slices = [list(range(i * size, min(total, (i + 1) * size))) for i in range(workers)]
        t0 = time.perf_counter()
        await asyncio.gather(*(render_slice(b, s, out) for b, s in zip(browsers, slices, strict=True)))
        elapsed = time.perf_counter() - t0
        for b in browsers:
            await b.close()
    return out, elapsed


def encode(frames: dict[int, bytes], path: Path, fps: int) -> float:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SystemExit("ffmpeg not found on PATH")
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "image2pipe",
            "-framerate",
            str(fps),
            "-c:v",
            "png",
            "-i",
            "-",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            "-preset",
            "medium",
            "-movflags",
            "+faststart",
            str(path),
        ],
        stdin=subprocess.PIPE,
    )
    assert proc.stdin is not None
    for f in sorted(frames):
        proc.stdin.write(frames[f])
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("ffmpeg failed")
    return time.perf_counter() - t0


def probe_duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    res = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(res.stdout.strip())


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=180)
    ap.add_argument("--workers", type=int, nargs="*", default=[1, 2, 4])
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    results = []
    reference: dict[int, bytes] | None = None
    for w in args.workers:
        frames, elapsed = await render(args.frames, w)
        results.append(
            {
                "workers": w,
                "frames": args.frames,
                "seconds": round(elapsed, 2),
                "fps": round(args.frames / elapsed, 1),
            }
        )
        reference = reference or frames

    assert reference is not None
    again, _ = await render(args.frames, 2)
    mismatched = [
        f for f in reference if hashlib.sha256(reference[f]).digest() != hashlib.sha256(again[f]).digest()
    ]

    mp4 = OUT / "spike.mp4"
    encode_s = encode(reference, mp4, 30)
    report = {
        "throughput": results,
        "determinism": {"frames": len(reference), "mismatched": len(mismatched)},
        "encode": {
            "seconds": round(encode_s, 2),
            "mp4": str(mp4.relative_to(HERE.parents[1])),
            "durationS": probe_duration(mp4),
        },
        "avgPngKB": round(sum(len(b) for b in reference.values()) / len(reference) / 1024, 1),
    }
    print(json.dumps(report, indent=2))
    (OUT / "report.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
