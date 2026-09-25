"""Event loop factory for uvicorn (`--loop animator.api.loop:loop_factory`).

The API starts the engine worker as a subprocess. With `--reload` (or several
workers) on Windows, uvicorn picks the selector event loop, which cannot spawn
subprocesses, so startup fails with NotImplementedError. This factory keeps the
proactor loop on Windows and the standard loop elsewhere.
"""

from __future__ import annotations

import asyncio
import sys


def loop_factory() -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        return asyncio.ProactorEventLoop()
    return asyncio.new_event_loop()
