"""Doubts over HTTP (ADR-050). Its own routes under /api/doubts; nothing else in the API depends on it.

- GET  /api/doubts/config                  whether doubts are on, with a voice, with speech-to-text
- GET  /api/doubts/phrases?language=...    the tutor's fixed lines (greeting, thinking, ...) with audio
- POST /api/doubts/transcribe              speech to text (browsers without speech recognition)
- POST /api/doubts/{lesson}                ask (a new doubt, or a follow-up with `session_id`)
- GET  /api/doubts/{lesson}/{doubt}        a doubt so far
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from animator.doubts.models import AskRequest
from animator.doubts.service import DoubtError, DoubtService

router = APIRouter(prefix="/api/doubts", tags=["doubts"])

LESSON = r"^g_[0-9a-f]{12}$"
DOUBT = r"^d_[0-9a-f]{12}$"
MAX_AUDIO = 10 * 1024 * 1024


def _service(request: Request) -> DoubtService:
    svc: DoubtService | None = getattr(request.app.state, "doubts", None)
    if svc is None:
        raise HTTPException(404, "Doubts are turned off (ANIMATOR_DOUBTS_ENABLED).")
    return svc


@router.get("/config")
async def config(request: Request) -> dict[str, Any]:
    svc: DoubtService | None = getattr(request.app.state, "doubts", None)
    if svc is None:
        return {"enabled": False}
    s = svc.settings
    return {
        "enabled": True,
        "voice": svc.speech is not None,
        "transcribe": s.has_openai_key,
        "max_turns": s.animator_doubt_max_turns,
    }


@router.get("/phrases")
async def phrases(request: Request, language: str = "English") -> dict[str, Any]:
    return await _service(request).phrases(language[:40])


@router.post("/transcribe")
async def transcribe(request: Request) -> dict[str, str]:
    svc = _service(request)
    audio = await request.body()
    if len(audio) > MAX_AUDIO:
        raise HTTPException(413, "The recording is too long.")
    try:
        text = await svc.transcribe(
            audio,
            request.headers.get("content-type", "audio/webm"),
            request.headers.get("x-lesson-language", "English")[:40],
        )
    except DoubtError as e:
        raise HTTPException(e.status, e.message) from e
    return {"text": text}


@router.post("/{gen_id}")
async def ask(request: Request, gen_id: str, body: AskRequest) -> dict[str, Any]:
    if not re.match(LESSON, gen_id):
        raise HTTPException(404, "No such lesson.")
    try:
        return await _service(request).ask(gen_id, body)
    except DoubtError as e:
        raise HTTPException(e.status, e.message) from e


@router.get("/{gen_id}/{session_id}")
async def doubt(request: Request, gen_id: str, session_id: str) -> dict[str, Any]:
    if not re.match(LESSON, gen_id) or not re.match(DOUBT, session_id):
        raise HTTPException(404, "No such doubt.")
    data = _service(request).get(gen_id, session_id)
    if data is None:
        raise HTTPException(404, "No such doubt.")
    return data
