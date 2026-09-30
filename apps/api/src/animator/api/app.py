"""FastAPI application.

`create_app()` wires the engine worker pool, the generation store, the LLM client, the pipeline,
the job runner and export jobs; tests pass fakes (FakeLLM, MemoryStore). `app` is what uvicorn
serves. The OpenAI key stays in the LLM client: no endpoint returns it.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from animator.api.generations import router as generations_router
from animator.api.tts import router as tts_router
from animator.config import STAGES, Settings, get_settings
from animator.engine_client import EngineClient, EngineWorkerPool, pool_from_settings
from animator.export.jobs import BrowserKeyframes, ExportJobs
from animator.export.site import WEB_DIST
from animator.jobs.runner import GenerationRunner
from animator.jobs.store import FileStore, GenerationStore, MemoryStore
from animator.llm import LLMClient, build_llm
from animator.observability import setup_logging
from animator.pipeline.generator import KeyframeSource, Pipeline
from animator.tts import TTSProvider, build_speech, tts_problem

log = logging.getLogger("animator.api")


def create_app(
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    store: GenerationStore | None = None,
    keyframes: KeyframeSource | None = None,
    resume_on_start: bool = True,
    tts: TTSProvider | None = None,
) -> FastAPI:
    s = settings or get_settings()
    setup_logging(s.log_level, s.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = pool_from_settings(s)
        app.state.engine_version = await pool.start()
        app.state.engine = pool
        engine = EngineClient(pool)
        the_store = store or (
            FileStore(s.resolve(s.animator_data_dir))
            if s.animator_storage_backend == "files"
            else MemoryStore()
        )
        frames = keyframes
        owned_frames: BrowserKeyframes | None = None
        if frames is None and s.animator_enable_visual_critic and (WEB_DIST / "index.html").exists():
            owned_frames = BrowserKeyframes()
            frames = owned_frames
        # Narration audio: None unless ANIMATOR_TTS_ENABLED (then nothing speaks anywhere).
        speech = build_speech(s, tts)
        pipeline = Pipeline(engine, llm or build_llm(), s, frames, speech)
        runner = GenerationRunner(the_store, pipeline, s)
        app.state.settings = s
        app.state.speech = speech
        app.state.runner = runner
        app.state.exports = ExportJobs(s)
        if resume_on_start:
            resumed = await runner.resume_all()
            if resumed:
                log.info("resumed %d generation(s): %s", len(resumed), ", ".join(resumed))
        try:
            yield
        finally:
            await runner.close()
            await app.state.exports.close()
            if owned_frames is not None:
                await owned_frames.close()
            if speech is not None:
                await speech.close()
            await pool.close()

    app = FastAPI(title="Animator API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in s.cors_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(generations_router)
    app.include_router(tts_router)

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        pool: EngineWorkerPool = request.app.state.engine
        workers = await pool.health()
        return {
            "ok": all(workers),
            "engine": request.app.state.engine_version,
            "workers": workers,
            "restarts": pool.restarts,
        }

    @app.get("/api/config")
    async def config(request: Request) -> dict[str, Any]:
        """What the pipeline will use (never the key itself)."""
        st: Settings = request.app.state.settings
        stages = {}
        for stage in STAGES:
            try:
                chain, effort = st.stage_model(stage)
            except ValueError:
                chain, effort = [], "none"
            stages[stage] = {"models": chain, "effort": effort}
        return {
            "openai_key_configured": st.has_openai_key,
            "stages": stages,
            "budget_usd_per_generation": st.llm_budget_usd_per_generation,
            "max_correction_attempts": st.animator_max_correction_attempts,
            "llm_critic": st.animator_enable_llm_critic,
            "visual_critic": st.animator_enable_visual_critic,
            "storage": st.animator_storage_backend,
            "tts": {
                "enabled": request.app.state.speech is not None,
                "provider": st.animator_tts_provider if st.animator_tts_enabled else None,
                "voice": request.app.state.speech.provider.voice if request.app.state.speech else None,
                "problem": tts_problem(st),
            },
        }

    return app


app = create_app()
