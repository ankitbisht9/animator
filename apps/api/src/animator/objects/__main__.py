"""Saved objects from the command line (ADR-055).

uv run python -m animator.objects build    # library/src -> library/*.json (fails on a bad drawing)
uv run python -m animator.objects check    # the built files match their sources (CI)
uv run python -m animator.objects list     # what lessons can use now (library + warehouse)
"""

from __future__ import annotations

import sys

from animator.config import get_settings
from animator.objects import library
from animator.objects.store import ObjectStore


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "list"
    if cmd == "build":
        built = library.build()
        for oid, b in built.items():
            state = "ok" if b.obj is not None and not b.problems else "FAILED"
            score = f" score {b.obj.quality.score:.2f}" if b.obj and b.obj.quality else ""
            print(f"{state:6} {oid}{score}")
            for p in b.problems:
                print(f"         - {p}")
            for w in b.warnings:
                print(f"         ~ {w}")
        failed = library.write(built)
        print(f"{len(built) - len(failed)} built, {len(failed)} failed")
        return 1 if failed else 0
    if cmd == "check":
        stale = library.drift(library.build())
        if stale:
            print("out of date (run: python -m animator.objects build): " + ", ".join(stale))
            return 1
        print("library is up to date")
        return 0
    if cmd == "list":
        settings = get_settings()
        store = ObjectStore(settings.resolve(settings.animator_objects_dir))
        for o in sorted(store.all(), key=lambda o: (o.status, o.id)):
            print(f"{o.status:9} {o.origin:8} {o.id:28} {o.title}")
        for e in store.errors:
            print(f"! {e}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
