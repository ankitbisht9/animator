"""Export a lesson to MP4 (and optionally WebM).

    uv run python -m animator.export --lesson archimedes
    uv run python -m animator.export --bundle var/exports/archimedes/archimedes.animation.json --name again
    uv run python -m animator.export --lesson gallery --scenes 2 --width 1280 --height 720

Segments already rendered with the same inputs are reused, so exporting a few scenes
first and the rest later only renders each scene once.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from animator.config import get_settings
from animator.export.exporter import AudioSource, ExportError, FileAudio, load_bundle
from animator.export.ffmpeg import FfmpegError
from animator.export.plan import ExportSettings, parse_formats, parse_scene_selection
from animator.export.render import load_fixture_lesson
from animator.export.service import export_lesson


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m animator.export", description=__doc__.split("\n\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--lesson", help="fixture lesson slug (fixtures/scenes/<slug>)")
    src.add_argument("--bundle", type=Path, help="an .animation.json bundle from an earlier export")
    ap.add_argument("--name", help="output name (default: the lesson slug or bundle name)")
    ap.add_argument("--out", type=Path, help="output directory (default: EXPORT_OUTPUT_DIR)")
    ap.add_argument("--scenes", help="only these scenes, 1-based, e.g. '1,3-5' (segments only)")
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--fps", type=int)
    ap.add_argument("--crf", type=int)
    ap.add_argument("--preset")
    ap.add_argument("--workers", type=int, help="parallel browsers")
    ap.add_argument("--formats", help="comma-separated: mp4,webm")
    ap.add_argument("--audio", type=Path, help="audio track to mux (padded/trimmed to the video)")
    return ap


def _log(message: str) -> None:
    print(message, flush=True)  # progress stays live when output goes to a file or pipe


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.lesson:
            lesson = load_fixture_lesson(args.lesson)
            name = args.name or args.lesson
        else:
            lesson = load_bundle(args.bundle)
            name = args.name or args.bundle.name.removesuffix(".animation.json")
        settings = ExportSettings.from_settings(
            get_settings(),
            width=args.width,
            height=args.height,
            fps=args.fps,
            crf=args.crf,
            preset=args.preset,
            workers=args.workers,
            formats=parse_formats(args.formats) if args.formats else None,
        )
        scenes = parse_scene_selection(args.scenes, len(lesson["scenes"])) if args.scenes else None
        audio: AudioSource | None = FileAudio(args.audio) if args.audio else None
        result = asyncio.run(export_lesson(lesson, name, settings, args.out, scenes, audio, log=_log))
    except (ExportError, FfmpegError, FileNotFoundError, ValueError) as e:
        print(f"export failed: {e}", file=sys.stderr)
        return 1
    if result.complete:
        for fmt, path in result.outputs.items():
            print(f"{fmt}: {path}")
    print(f"report: {result.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
