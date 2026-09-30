"""Narration audio files (TTS on): `/api/tts/<id>.mp3`, named by content, so cached forever."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from animator.tts import SpeechCache

router = APIRouter(prefix="/api/tts", tags=["tts"])

MEDIA = {"mp3": "audio/mpeg", "wav": "audio/wav"}


@router.get("/{name}")
async def audio(name: str, request: Request) -> FileResponse:
    speech: SpeechCache | None = request.app.state.speech
    path = speech.audio_path(name) if speech is not None else None
    if path is None:
        raise HTTPException(404, "no such narration audio")
    return FileResponse(
        path,
        media_type=MEDIA[path.suffix[1:]],
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
