"""Generation endpoints: create, list, inspect, live progress (SSE), cancel, resume, playhead,
LLM call log, and MP4 export of a finished lesson."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from animator.export.jobs import ExportJobs
from animator.export.narration import NarrationAudio
from animator.export.plan import ExportSettings
from animator.jobs.runner import GenerationRunner
from animator.pipeline.models import TERMINAL, Generation, GenerationRequest

router = APIRouter(prefix="/api/generations", tags=["generations"])


def _runner(request: Request) -> GenerationRunner:
    runner: GenerationRunner = request.app.state.runner
    return runner


async def _gen(request: Request, gen_id: str) -> Generation:
    gen = await _runner(request).get(gen_id)
    if gen is None:
        raise HTTPException(404, f"no generation '{gen_id}'")
    return gen


def _speaks(request: Request) -> bool:
    """Narration audio is on (ANIMATOR_TTS_ENABLED). When off, no narration leaves the API, so
    lessons made with a voice play and export exactly as before, captions only."""
    return request.app.state.speech is not None


def _playable(gen: Generation, speaks: bool) -> list[dict[str, Any]]:
    return [
        {
            "index": s.index,
            "id": s.id,
            "status": s.status,
            "duration": s.duration,
            "dsl": s.dsl,
            "narration": s.narration if speaks else None,
        }
        for s in gen.scenes
        if s.playable
    ]


@router.post("", status_code=201)
async def create(body: GenerationRequest, request: Request) -> dict[str, Any]:
    if body.trial_model is not None and body.trial_model not in request.app.state.settings.trial_models:
        raise HTTPException(422, f"'{body.trial_model}' is not a trial model (ANIMATOR_TRIAL_MODELS).")
    gen = await _runner(request).start(body)
    return gen.summary()


@router.get("")
async def list_generations(request: Request, limit: int = 30) -> list[dict[str, Any]]:
    gens = await _runner(request).store.recent(limit=max(1, min(limit, 200)))
    return [(await _runner(request).get(g.id) or g).summary() for g in gens]


@router.get("/{gen_id}")
async def get_generation(gen_id: str, request: Request) -> dict[str, Any]:
    gen = await _gen(request, gen_id)
    data = gen.model_dump(mode="json")
    if not _speaks(request):
        for s in data["scenes"]:
            s["narration"] = None
    data["summary"] = gen.summary()
    data["running"] = _runner(request).running(gen_id)
    return data


@router.get("/{gen_id}/events")
async def events(gen_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events: a snapshot (progress plus every playable scene), then live updates."""
    runner = _runner(request)
    gen = await _gen(request, gen_id)

    async def stream() -> AsyncIterator[str]:
        async with runner.hub.subscribe(gen_id) as queue:
            current = await runner.get(gen_id) or gen
            final = current.status in TERMINAL and not runner.running(gen_id)
            snapshot = {
                "type": "snapshot",
                "generation": current.summary(),
                "scenes": _playable(current, _speaks(request)),
                "final": final,
            }
            yield f"data: {json.dumps(snapshot)}\n\n"
            if final:
                return  # nothing more will happen: the client has everything
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    latest = await runner.get(gen_id)
                    if latest is not None and latest.status in TERMINAL and not runner.running(gen_id):
                        # The run ended between the snapshot and this subscription: close it now.
                        done = {"type": "update", "generation": latest.summary(), "final": True}
                        yield f"data: {json.dumps(done)}\n\n"
                        return
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
                # A lesson is ready before drafts that lost the race finish; the stream stays open
                # until the run is over, so the final cost reaches the page.
                if event.get("final"):
                    return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{gen_id}/cancel")
async def cancel(gen_id: str, request: Request) -> dict[str, Any]:
    await _gen(request, gen_id)
    gen = await _runner(request).cancel(gen_id)
    assert gen is not None
    return gen.summary()


@router.post("/{gen_id}/resume")
async def resume(gen_id: str, request: Request) -> dict[str, Any]:
    await _gen(request, gen_id)
    gen = await _runner(request).resume(gen_id)
    assert gen is not None
    return gen.summary()


class Playhead(BaseModel):
    t: float = Field(ge=0)


@router.post("/{gen_id}/playhead", status_code=204)
async def playhead(gen_id: str, body: Playhead, request: Request) -> None:
    await _gen(request, gen_id)
    _runner(request).playhead(gen_id, body.t)


@router.get("/{gen_id}/calls")
async def calls(gen_id: str, request: Request) -> list[dict[str, Any]]:
    await _gen(request, gen_id)
    return await _runner(request).store.calls(gen_id)


# ---------------------------------------------------------------------- export


class ExportBody(BaseModel):
    width: int = 1280
    height: int = 720
    fps: int = 30


@router.post("/{gen_id}/export", status_code=202)
async def start_export(gen_id: str, request: Request, body: ExportBody | None = None) -> dict[str, Any]:
    gen = await _gen(request, gen_id)
    if gen.status != "complete" or not all(s.playable for s in gen.scenes):
        raise HTTPException(409, "the lesson is not finished generating yet")
    b = body or ExportBody()
    jobs: ExportJobs = request.app.state.exports
    settings = ExportSettings.from_settings(
        request.app.state.settings, width=b.width, height=b.height, fps=b.fps
    )
    lesson: dict[str, Any] = {
        "title": gen.plan.title if gen.plan else gen.request.topic,
        "scenes": [s.dsl for s in gen.scenes],
    }
    audio = None
    speech = request.app.state.speech
    if speech is not None and any(s.narration for s in gen.scenes):
        # The voice goes into the video, and the captions follow it word by word.
        lesson["narration"] = [s.narration for s in gen.scenes]
        audio = NarrationAudio(speech.directory, request.app.state.settings.ffmpeg_path)
    return jobs.start(gen_id, lesson, settings, audio).to_json()


@router.get("/{gen_id}/export")
async def export_status(gen_id: str, request: Request) -> dict[str, Any]:
    await _gen(request, gen_id)
    job = request.app.state.exports.get(gen_id)
    if job is None:
        raise HTTPException(404, "no export for this generation")
    data: dict[str, Any] = job.to_json()
    return data


@router.get("/{gen_id}/export/video")
async def export_video(gen_id: str, request: Request) -> FileResponse:
    await _gen(request, gen_id)
    job = request.app.state.exports.get(gen_id)
    if job is None or job.video is None or not job.video.exists():
        raise HTTPException(404, "no exported video yet")
    return FileResponse(job.video, media_type="video/mp4", filename=f"{gen_id}.mp4")
