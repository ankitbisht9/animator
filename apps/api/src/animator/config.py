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
    openai_timeout_s: float = 120.0
    openai_max_retries: int = 4
    llm_max_concurrency: int = 4
    llm_budget_usd_per_generation: float = 5.0
    llm_budget_tokens_per_generation: int = 2_000_000

    # Per-stage model assignment. Empty means "not configured" and fails loudly when used.
    animator_model_educational_planner: str = ""
    animator_effort_educational_planner: ReasoningEffort = "high"
    animator_model_storyboard: str = ""
    animator_effort_storyboard: ReasoningEffort = "high"
    animator_model_animation_planner: str = ""
    animator_effort_animation_planner: ReasoningEffort = "high"
    animator_model_correction: str = ""
    animator_effort_correction: ReasoningEffort = "high"
    animator_model_correction_final: str = ""
    animator_effort_correction_final: ReasoningEffort = "high"
    animator_model_critic: str = ""
    animator_effort_critic: ReasoningEffort = "medium"
    animator_model_visual_critic: str = ""
    animator_effort_visual_critic: ReasoningEffort = "medium"
    animator_model_lightweight: str = ""
    animator_effort_lightweight: ReasoningEffort = "low"
    animator_pricing_file: Path = Path("config/pricing.json")

    # --- Pipeline (used from Phase 8) ---
    animator_max_correction_attempts: int = 3
    animator_enable_llm_critic: bool = False
    animator_enable_visual_critic: bool = False
    animator_chunk_target_duration_s: float = 75.0
    animator_chunk_max_duration_s: float = 120.0
    animator_buffer_target_seconds: float = 90.0
    animator_min_ready_scenes: int = 2
    animator_duration_tolerance: float = 0.15

    # --- Storage: no database for now (see ADR-003) ---
    animator_storage_backend: Literal["memory", "files"] = "memory"
    animator_data_dir: Path = Path("var")

    # --- Engine worker ---
    engine_worker_node: str = "node"
    engine_worker_script: Path = Path("packages/engine-worker/dist/worker.cjs")
    engine_worker_pool_size: int = Field(default=2, ge=1, le=32)
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
