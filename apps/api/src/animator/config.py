"""Runtime configuration, read from environment variables (and `.env` at the repo root).

Model IDs, reasoning efforts and prices are configuration, never constants in source.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from animator import REPO_ROOT

ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]

STAGES = (
    "educational_planner",
    "storyboard",
    "animation_planner",
    "correction",
    "correction_final",
    "critic",
    "visual_critic",
    "lightweight",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- OpenAI (used from Phase 7) ---
    openai_api_key: SecretStr | None = None
    openai_org_id: str | None = None
    openai_project_id: str | None = None
    openai_base_url: str | None = None
    openai_store: bool = False
    # Processing tier: empty = the project's default; "priority" (Fast mode) is about 1.5x faster for 2x
    # the price; "flex" is half price but slower. Prices per tier: config/pricing.json.
    openai_service_tier: str | None = None
    # A scene takes about 20 s at low effort (1-2 min at high): raise this with the effort.
    openai_timeout_s: float = 120.0
    openai_max_retries: int = 4
    llm_max_concurrency: int = 16
    llm_budget_usd_per_generation: float = 5.0
    llm_budget_tokens_per_generation: int = 2_000_000

    # Per-stage model assignment. Empty means "not configured" and fails loudly when used.
    # Efforts default to low: generation must keep up with playback (ADR-033).
    animator_model_educational_planner: str = ""
    animator_effort_educational_planner: ReasoningEffort = "low"
    animator_model_storyboard: str = ""
    animator_effort_storyboard: ReasoningEffort = "low"
    animator_model_animation_planner: str = ""
    animator_effort_animation_planner: ReasoningEffort = "low"
    animator_model_correction: str = ""
    animator_effort_correction: ReasoningEffort = "low"
    animator_model_correction_final: str = ""
    animator_effort_correction_final: ReasoningEffort = "low"
    animator_model_critic: str = ""
    animator_effort_critic: ReasoningEffort = "medium"
    animator_model_visual_critic: str = ""
    animator_effort_visual_critic: ReasoningEffort = "medium"
    animator_model_lightweight: str = ""
    animator_effort_lightweight: ReasoningEffort = "low"
    animator_pricing_file: Path = Path("config/pricing.json")
    # Tried in order when a stage's model is unavailable to this API key (404/403).
    animator_model_fallbacks: str = ""
    animator_models_file: Path = Path("config/models.json")
    # openai = live calls; record = live calls saved as replay fixtures; replay = offline from fixtures.
    animator_llm_provider: Literal["openai", "record", "replay"] = "openai"
    animator_llm_fixtures_dir: Path = Path("fixtures/llm/latest")

    # --- Pipeline (used from Phase 8) ---
    animator_max_correction_attempts: int = 3  # patch rounds take seconds, so a third is affordable
    # Tidy each valid scene's layout with the engine's hints (labels off objects, arrows bent around
    # things, nothing under the caption); no model call, a fraction of a second per scene.
    animator_layout_polish: bool = True
    # Corrections reply with only what changes (a few hundred tokens) instead of the whole scene.
    animator_patch_corrections: bool = True
    animator_enable_llm_critic: bool = False
    animator_enable_visual_critic: bool = False
    animator_chunk_target_duration_s: float = 75.0
    animator_chunk_max_duration_s: float = 120.0
    animator_buffer_target_seconds: float = 90.0
    animator_min_ready_scenes: int = 2
    animator_duration_tolerance: float = 0.15
    animator_generation_concurrency: int = Field(default=2, ge=1, le=16)
    # parallel: every scene is drawn on its own (clean stage) and all scenes are generated at once (fast);
    # sequential: each scene continues from the previous scene's end state (objects carry over; slow).
    animator_scene_mode: Literal["parallel", "sequential"] = "parallel"
    animator_scene_concurrency: int = Field(default=8, ge=1, le=32)
    # Drafts written at once for each scene; the first valid one wins (fewer correction rounds, so
    # faster and steadier, for about 1.5x the scene cost). 1 = a single draft.
    animator_scene_drafts: int = Field(default=2, ge=1, le=4)
    # The same for each correction round (patches are small: about $0.002 each).
    animator_correction_drafts: int = Field(default=2, ge=1, le=4)
    # Lessons up to this long are planned and storyboarded in one call (saves a round-trip).
    animator_outline_max_duration_s: float = 150.0
    animator_scene_max_output_tokens: int = 48_000
    animator_visual_critic_keyframes: int = Field(default=3, ge=1, le=8)

    # --- Storage: no database for now (see ADR-003) ---
    animator_storage_backend: Literal["memory", "files"] = "files"
    animator_data_dir: Path = Path("var")

    # --- Engine worker ---
    engine_worker_node: str = "node"
    engine_worker_script: Path = Path("packages/engine-worker/dist/worker.cjs")
    engine_worker_pool_size: int = Field(default=4, ge=1, le=32)
    engine_worker_call_timeout_s: float = 30.0
    engine_worker_max_restarts: int = 5

    # --- API server ---
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    # --- Export ---
    export_fps: int = 30
    export_width: int = 1920
    export_height: int = 1080
    export_crf: int = 18
    export_workers: int = 4
    export_formats: str = "mp4"
    export_output_dir: Path = Path("var/exports")
    ffmpeg_path: str = "ffmpeg"

    def resolve(self, path: Path) -> Path:
        """Resolve a repo-relative path from configuration."""
        return path if path.is_absolute() else REPO_ROOT / path

    def stage_model(self, stage: str) -> tuple[list[str], ReasoningEffort]:
        """Model chain (configured model first, then the fallbacks) and effort for a stage."""
        if stage not in STAGES:
            raise ValueError(f"unknown pipeline stage '{stage}'")
        primary = str(getattr(self, f"animator_model_{stage}")).strip()
        effort: ReasoningEffort = getattr(self, f"animator_effort_{stage}")
        chain = [primary] if primary else []
        for m in self.animator_model_fallbacks.split(","):
            m = m.strip()
            if m and m not in chain:
                chain.append(m)
        if not chain:
            raise ValueError(f"no model configured for stage '{stage}' (ANIMATOR_MODEL_{stage.upper()})")
        return chain, effort

    @property
    def has_openai_key(self) -> bool:
        return self.openai_api_key is not None and bool(self.openai_api_key.get_secret_value().strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
