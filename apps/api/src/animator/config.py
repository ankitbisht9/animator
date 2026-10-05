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
    "teacher",
    "reviewer",
    "director",
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

    # Per-stage model assignment. Empty means "not configured" and fails loudly when used (the
    # teaching stages then use the storyboard's model). The teaching stages think at medium effort:
    # the explanation is the product (ADR-044); the fast pipeline's stages default to low (ADR-033).
    animator_model_teacher: str = ""
    animator_effort_teacher: ReasoningEffort = "medium"
    animator_model_reviewer: str = ""
    animator_effort_reviewer: ReasoningEffort = "low"
    animator_model_director: str = ""
    animator_effort_director: ReasoningEffort = "medium"
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
    # teaching (ADR-044): a teacher writes the whole explanation, a reviewer checks and improves it, a
    # visual director plans every scene's picture, then the scenes are drawn (about 1-2 minutes).
    # fast: plan and storyboard in one call (ADR-033, about 30 seconds).
    animator_pipeline: Literal["teaching", "fast"] = "teaching"
    # The reviewer's pass over the teacher's script (teaching pipeline).
    animator_script_review: bool = True
    # Longer lessons are planned in chapters by the fast pipeline's planner.
    animator_teaching_max_duration_s: float = 600.0
    # A teaching lesson's scene drafts think at this effort (medium: about twice as many valid,
    # glitch-free first drafts as low, at twice the time; ADR-041's benchmark). Corrections keep
    # ANIMATOR_EFFORT_CORRECTION.
    animator_effort_teaching_scene: ReasoningEffort = "medium"
    # A teaching lesson's valid scenes are rendered and looked at by a vision model, which can ask
    # for one round of fixes (needs the built web app; ANIMATOR_ENABLE_VISUAL_CRITIC does the same
    # for every lesson).
    animator_teaching_visual_critic: bool = True
    # How many times a teaching scene is reviewed (each review can ask for one round of fixes).
    animator_teaching_critic_rounds: int = Field(default=1, ge=1, le=3)
    # A teaching lesson's valid scenes are framed by the camera: their content centred on the stage
    # and zoomed in (up to 1.5x) to fill it, when the engine confirms the framed scene is clean.
    animator_auto_frame: bool = True
    # A teaching lesson's labels that still cover, hide under or are crossed by something after the
    # polish are tried in other places around what they name; the engine judges every try (ADR-046).
    animator_label_relocation: bool = True
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

    # --- Narration audio (text to speech, ADR-037) ---
    # The one switch: 0 = no speech anywhere (pipeline, API, player, export behave as before).
    animator_tts_enabled: bool = False
    # edge: free Microsoft neural voices, no key | azure | google | elevenlabs | kokoro (a local
    # Kokoro-FastAPI server, free) | openai (OpenAI or any OpenAI-compatible server) | fake (tests).
    animator_tts_provider: Literal["edge", "azure", "google", "elevenlabs", "kokoro", "openai", "fake"] = (
        "edge"
    )
    animator_tts_voice: str = ""  # empty = the provider's default voice
    # The provider and voice of Hinglish lessons; empty = the main provider and its Hinglish default
    # (ADR-038, ADR-039). Kokoro reads Roman-script Hinglish with English sounds: use edge for it.
    animator_tts_provider_hinglish: Literal[
        "", "edge", "azure", "google", "elevenlabs", "kokoro", "openai", "fake"
    ] = ""
    animator_tts_voice_hinglish: str = ""
    animator_tts_speed: float = Field(default=1.0, ge=0.5, le=2.0)
    animator_tts_model: str = ""  # elevenlabs / openai model; empty = the provider's default
    animator_tts_instructions: str = ""  # tone of voice for OpenAI gpt-4o-mini-tts
    # kokoro: the Kokoro server (empty = http://localhost:8880); openai: empty = api.openai.com.
    animator_tts_base_url: str = ""
    animator_tts_api_key: SecretStr | None = None  # openai provider: empty = OPENAI_API_KEY
    azure_speech_key: SecretStr | None = None
    azure_speech_region: str = ""
    google_tts_api_key: SecretStr | None = None
    elevenlabs_api_key: SecretStr | None = None
    # One take (ADR-043): each scene's narration is spoken in one recording, so the voice flows from
    # sentence to sentence with only the pauses its punctuation makes. Off: sentence by sentence.
    animator_tts_one_take: bool = True
    # Quiet between two scenes: from the last word of a scene to the next scene (one take).
    animator_tts_scene_pause_s: float = Field(default=1.0, ge=0.0, le=3.0)
    # Sentence by sentence only: pause from the last word of one sentence to the next (ADR-041).
    animator_tts_pause_s: float = Field(default=0.35, ge=0.0, le=3.0)
    # Fit each sentence's time to its speech (shorten long silences as well as lengthen short beats),
    # when the engine confirms the animation still works; otherwise beats only grow (ADR-041).
    animator_tts_fit_beats: bool = True
    # The speech script (ADR-041): hinglish = Hindi words go to the voice in Devanagari so they are
    # pronounced right ("sunati", not "sunti"), with spoken punctuation and a tone; all = also for
    # English lessons (punctuation and tone); off = the captions are read as written.
    animator_tts_script: Literal["off", "hinglish", "all"] = "hinglish"
    # Voice modulation (ADR-045): the speech script also directs how each sentence is said (tone,
    # pace, stressed words, pauses before key points), for every lesson language; models that take
    # audio tags (Eleven v3, v4) get it in the text, and a teacher's timing is applied to the audio
    # of every model: the least pause after a full stop, a comma and before a key point, and the
    # tempo of a slow sentence (0.9 = 10% slower, pitch unchanged). Off: speech as before.
    animator_tts_prosody: bool = True
    animator_tts_sentence_pause_s: float = Field(default=0.4, ge=0.0, le=2.0)
    animator_tts_comma_pause_s: float = Field(default=0.18, ge=0.0, le=1.0)
    animator_tts_key_pause_s: float = Field(default=0.35, ge=0.0, le=2.0)
    animator_tts_slow_tempo: float = Field(default=0.9, ge=0.7, le=1.0)
    # With voice modulation: a beat's highlights move to the moments its stressed words are said.
    animator_tts_sync_highlights: bool = True
    # ElevenLabs delivery: lower stability is livelier and more human, style adds expression.
    animator_tts_stability: float = Field(default=0.4, ge=0.0, le=1.0)
    animator_tts_style: float = Field(default=0.3, ge=0.0, le=1.0)
    animator_tts_concurrency: int = Field(default=8, ge=1, le=32)
    animator_tts_timeout_s: float = 30.0
    animator_tts_cache_dir: Path = Path("var/tts")

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
        if not primary and stage in ("teacher", "reviewer", "director"):
            primary = self.animator_model_storyboard.strip()
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
