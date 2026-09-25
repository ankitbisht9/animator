"""Render keyframes (scene start, each beat end, scene end) of a fixture lesson to PNGs.

    uv run python scripts/render_keyframes.py archimedes [--width 1280] [--times 1.5 3]

Output: var/keyframes/<lesson>/<NN>-<scene id>-<keyframe>.png. Needs `make build-web`.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from animator import REPO_ROOT
from animator.export.render import RenderSession, Viewport, keyframe_times, load_fixture_lesson, write_png
from animator.export.site import StaticSite


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("lesson")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--scene", type=int, default=None, help="only this scene index")
    ap.add_argument("--times", type=float, nargs="*", default=None, help="explicit scene-local times")
    args = ap.parse_args()
    out_dir = REPO_ROOT / "var" / "keyframes" / args.lesson
    lesson = load_fixture_lesson(args.lesson)
    with StaticSite() as site:
        async with async_playwright() as pw:
            session = await RenderSession(site.url, Viewport(args.width, args.width * 9 // 16)).start(pw)
            summary = await session.load(lesson)
            for i, scene in enumerate(summary["scenes"]):
                if args.scene is not None and i != args.scene:
                    continue
                if scene["errors"]:
                    first = scene["errors"][0]["message"]
                    print(f"scene {i} {scene['id']}: {len(scene['errors'])} error(s): {first}")
                times = [(f"t{t:g}", t) for t in args.times] if args.times else keyframe_times(scene)
                for name, t in times:
                    path = out_dir / f"{i + 1:02d}-{scene['id']}-{name}.png"
                    write_png(path, await session.at_png(i, t))
                    print(Path(path).relative_to(REPO_ROOT).as_posix())
            await session.close()


if __name__ == "__main__":
    asyncio.run(main())
