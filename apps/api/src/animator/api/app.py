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

from animator import REPO_ROOT
from animator.api.generations import router as generations_router
from animator.api.tts import router as tts_router
from animator.config import STAGES, Settings, get_settings
from animator.doubts import DoubtService
from animator.doubts import router as doubts_router
from animator.engine_client import EngineClient, EngineWorkerPool, pool_from_settings
from animator.export.jobs import BrowserKeyframes, ExportJobs
from animator.export.site import WEB_DIST
from animator.jobs.runner import GenerationRunner
from animator.jobs.store import FileStore, GenerationStore, MemoryStore
from animator.llm import LLMClient, build_llm
from animator.objects.harvest import Harvester
from animator.observability import setup_logging
from animator.pipeline.generator import KeyframeSource, Pipeline
from animator.platform import Platform, PlatformSettings, serve_web
from animator.platform import install as install_platform
from animator.tts import TTSProvider, build_speech, env_duplicates, tts_problem

log = logging.getLogger("animator.api")


def create_app(
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    store: GenerationStore | None = None,
    keyframes: KeyframeSource | None = None,
    resume_on_start: bool = True,
    tts: TTSProvider | None = None,
    platform: PlatformSettings | None = None,
) -> FastAPI:
    s = settings or get_settings()
    setup_logging(s.log_level, s.log_format)
    # The .env the settings came from (checked for narration settings written twice); none when the
    # caller passes its own settings (tests).
    env_file = REPO_ROOT / ".env" if settings is None else None
    # The platform (ADR-051): accounts, roles and the gateway, from PLATFORM_* settings for the served
    # app. An app built with its own settings and no platform (the pipeline's tests) is as before.
    ps = platform if platform is not None else (PlatformSettings() if settings is None else None)
    plat = Platform(ps) if ps is not None and ps.enabled else None

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
        critic = s.animator_enable_visual_critic or (
            s.animator_pipeline == "teaching" and s.animator_teaching_visual_critic
        )
        if frames is None and critic and (WEB_DIST / "index.html").exists():
            owned_frames = BrowserKeyframes()
            frames = owned_frames
        # Narration audio: None unless ANIMATOR_TTS_ENABLED (then nothing speaks anywhere).
        speech = build_speech(s, tts, env_file)
        llm_client = llm or build_llm()
        pipeline = Pipeline(engine, llm_client, s, frames, speech)
        runner = GenerationRunner(the_store, pipeline, s)
        # Saved drawings (ADR-055): the library cost-saver lessons use, and the warehouse's harvest
        # of finished lessons (in the background when ANIMATOR_OBJECTS_HARVEST, else on request).
        app.state.objects = pipeline.objects
        app.state.harvester = (
            Harvester(pipeline.objects.store, engine, the_store, s, busy=runner.busy)
            if pipeline.objects is not None
            else None
        )
        if app.state.harvester is not None and s.animator_objects_harvest:
            app.state.harvester.start()
        app.state.settings = s
        app.state.speech = speech
        app.state.runner = runner
        # Doubts (ADR-050): an isolated feature reading lessons; nothing above depends on it.
        app.state.doubts = (
            DoubtService(engine, llm_client, s, speech, the_store) if s.animator_doubts_enabled else None
        )
        app.state.exports = ExportJobs(s)
        if plat is not None:
            await plat.start(runner, s, app.state.doubts, pool)
        if resume_on_start:
            resumed = await runner.resume_all()
            if resumed:
                log.info("resumed %d generation(s): %s", len(resumed), ", ".join(resumed))
        try:
            yield
        finally:
            if plat is not None:
                await plat.close()
            if app.state.harvester is not None:
                await app.state.harvester.close()
            await runner.close()
            await app.state.exports.close()
            if owned_frames is not None:
                await owned_frames.close()
            if speech is not None:
                await speech.close()
            await pool.close()

    app = FastAPI(title="Animator API", version="0.1.0", lifespan=lifespan)
    origins = [o.strip() for o in s.cors_origins.split(",") if o.strip()]
    if plat is not None:
        install_platform(app, plat, origins)  # inside CORS: preflights never reach the gateway
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
        allow_credentials=plat is not None,
    )
    app.include_router(generations_router)
    app.include_router(tts_router)
    app.include_router(doubts_router)

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
        speech = request.app.state.speech
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
            # How teaching lessons are laid out unless a lesson asks otherwise (ADR-047).
            "layout_mode": st.animator_layout_mode,
            "tts": {
                "enabled": speech is not None,
                "provider": st.animator_tts_provider if st.animator_tts_enabled else None,
                "voice": speech.provider.voice if speech else None,
                "voices": {"Hinglish": speech.voice_for("Hinglish")} if speech else {},
                "providers": {"Hinglish": speech.provider_for("Hinglish").name} if speech else {},
                "problem": tts_problem(st),
                "warnings": [
                    f"{name} is set more than once in .env: only the last one counts"
                    for name in (env_duplicates(env_file) if st.animator_tts_enabled else [])
                ],
            },
        }

    if plat is not None:
        serve_web(app, plat)  # last: the web app answers every path the API does not
    return app


app = create_app()
