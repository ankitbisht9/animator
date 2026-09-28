"""`make export-demo`: export the hand-authored Archimedes lesson to MP4.

Runs inside the pinned Docker export environment (docker/export.Dockerfile); settings
come from EXPORT_* environment variables (1080p, 30 fps by default).
"""

from __future__ import annotations

import sys

from animator.export.__main__ import main

if __name__ == "__main__":
    sys.exit(main(["--lesson", "archimedes", *sys.argv[1:]]))
