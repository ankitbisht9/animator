"""The uvicorn loop factory must give a loop that can start the engine worker subprocess."""

from __future__ import annotations

import asyncio
import sys

from animator.api.loop import loop_factory


def test_loop_factory_can_spawn_subprocesses() -> None:
    async def run() -> bytes:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "print('ok')", stdout=asyncio.subprocess.PIPE
        )
        out, _ = await proc.communicate()
        return out

    loop = loop_factory()
    try:
        assert loop.run_until_complete(run()).strip() == b"ok"
    finally:
        loop.close()
