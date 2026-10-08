"""Platform settings (ADR-051): accounts, roles, the gateway, the database. Read from `PLATFORM_*`
environment variables (and `.env`); separate from the pipeline's settings, which they never change."""

from __future__ import annotations

import contextlib
import json
import logging
import secrets
import time
from datetime import tzinfo
from functools import cached_property
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from animator import REPO_ROOT

log = logging.getLogger("animator.platform")


class PlatformSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PLATFORM_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # false: the API is as before the platform (no accounts, every route open). Development only.
    enabled: bool = True
    environment: Literal["development", "production"] = "development"

    # --- Database: SQLite in var/ by default (no server); any SQLAlchemy async URL works, e.g.
    # postgresql+asyncpg://user:pass@host/db (then add the asyncpg driver).
    database_url: str = ""
    data_dir: Path = Path("var/platform")
    db_echo: bool = False

    # --- Tokens. Empty secret: one is generated once and kept in <data_dir>/secret.key.
    secret_key: SecretStr | None = None
    access_ttl_min: int = Field(default=15, ge=1, le=120)
    refresh_ttl_days: int = Field(default=30, ge=1, le=365)
    # A sign-in without "keep me signed in" lasts this long (the cookie also ends with the browser).
    short_session_hours: int = Field(default=12, ge=1, le=72)
    # auto: Secure cookies when the request came over https (or in production).
    cookie_secure: Literal["auto", "true", "false"] = "auto"
    cookie_domain: str | None = None

    # --- Accounts
    # false: invite-only. Only `admin_emails` and `invite_emails` may sign up (a private demo).
    allow_signup: bool = True
    # Emails that are admins as soon as they sign up (comma separated). Or: python -m animator.platform
    # create-admin.
    admin_emails: str = ""
    # Who may sign up while sign-ups are closed, as learners: emails, or "@school.org" for a whole domain
    # (comma separated).
    invite_emails: str = ""
    # The first account on an empty platform becomes an admin. Empty: on in development (so the person
    # running it owns the lessons made before accounts), off in production (use create-admin).
    first_user_admin: bool | None = None
    require_verified_email: bool = False
    password_min_length: int = Field(default=8, ge=8, le=128)
    max_failed_logins: int = Field(default=5, ge=1)
    lockout_minutes: int = Field(default=15, ge=1)

    # --- Mail: SMTP when a host is set; otherwise mails are written to <data_dir>/outbox.
    public_base_url: str = "http://localhost:5173"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr | None = None
    smtp_from: str = "Animator <no-reply@localhost>"
    smtp_starttls: bool = True

    # --- Quotas per role, per day (0 = no limit) and the longest lesson a role may ask for.
    lessons_per_day_learner: int = 5
    lessons_per_day_educator: int = 25
    lessons_per_day_admin: int = 0
    max_minutes_learner: float = 10
    max_minutes_educator: float = 20
    max_minutes_admin: float = 20
    doubts_per_day_learner: int = 40
    doubts_per_day_educator: int = 120
    doubts_per_day_admin: int = 0

    # --- Credits and cost (ADR-053). Every spend (AI, voice, speech input) is recorded in dollars and
    # in credits: one credit is `credit_usd` dollars, at the price of the day it was spent.
    credit_usd: float = Field(default=0.01, gt=0, le=100)
    # Credits a person may spend per calendar month, by role (0 = no limit). An admin can give one
    # person an allowance of their own, no limit, or none.
    monthly_credits_learner: int = Field(default=1000, ge=0)
    monthly_credits_educator: int = Field(default=5000, ge=0)
    monthly_credits_admin: int = Field(default=0, ge=0)
    # The clock of the reports (today, this week from Monday, this month) and of the monthly
    # allowance: "local" (this computer's) or a time zone name such as "Asia/Kolkata" or "UTC".
    timezone: str = "local"
    # Voice prices in USD per million characters, by provider, over VOICE_PRICES below, as JSON:
    # PLATFORM_VOICE_PRICES={"elevenlabs": 165}. Voice and speech input are estimates (characters and
    # recording size); the AI's cost is counted from the tokens of each call.
    voice_prices: Annotated[dict[str, float], NoDecode] = Field(default_factory=dict)
    transcribe_usd_per_min: float = Field(default=0.003, ge=0)

    # --- Explore (ADR-053): a lesson shared with a link whose ratings average at least
    # `explore_min_average` from at least `explore_min_raters` people is listed in Explore by itself
    # (its owner can opt out). Admins can add any lesson or keep any out.
    explore_auto: bool = True
    explore_min_raters: int = Field(default=21, ge=1, le=100_000)
    explore_min_average: float = Field(default=4.0, ge=1, le=5)

    # --- Gateway
    # Requests per minute per signed-in user (or per address when signed out), on the whole API.
    rate_limit_per_min: int = 600
    max_body_bytes: int = 1_000_000
    max_upload_bytes: int = 12_000_000
    trusted_hosts: str = "*"
    # The interactive API docs (/docs): admins only; false hides them.
    docs: bool = True
    # Serve the built web app (apps/web/dist) from the API, one origin (production).
    serve_web: bool = False
    hsts: bool = False

    @field_validator("voice_prices", mode="before")
    @classmethod
    def _voice_prices(cls, v: Any) -> Any:
        """JSON from the environment; an empty value is no override (as `.env.example` leaves it)."""
        if isinstance(v, str):
            if not v.strip():
                return {}
            try:
                return json.loads(v)
            except ValueError as e:
                raise ValueError('PLATFORM_VOICE_PRICES must be JSON, e.g. {"elevenlabs": 165}') from e
        return v

    @property
    def production(self) -> bool:
        return self.environment == "production"

    def resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else REPO_ROOT / path

    @cached_property
    def directory(self) -> Path:
        d = self.resolve(self.data_dir)
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def url(self) -> str:
        if self.database_url.strip():
            return self.database_url.strip()
        return f"sqlite+aiosqlite:///{(self.directory / 'platform.db').as_posix()}"

    @cached_property
    def secret(self) -> str:
        """The token signing key: configured, or generated once and kept next to the database."""
        if self.secret_key is not None and self.secret_key.get_secret_value().strip():
            return self.secret_key.get_secret_value().strip()
        path = self.directory / "secret.key"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        key = secrets.token_urlsafe(48)
        path.write_text(key, encoding="utf-8")
        with contextlib.suppress(OSError):  # Windows ignores most modes
            path.chmod(0o600)
        return key

    @property
    def bootstrap_admin(self) -> bool:
        return (not self.production) if self.first_user_admin is None else self.first_user_admin

    @property
    def admin_list(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    def may_sign_up(self, email: str) -> bool:
        """Open sign-ups, or (closed) an admin email or an invited email or domain."""
        email = email.strip().lower()
        if self.allow_signup or email in self.admin_list:
            return True
        invited = {e.strip().lower() for e in self.invite_emails.split(",") if e.strip()}
        return email in invited or any(e.startswith("@") and email.endswith(e) for e in invited)

    def lessons_per_day(self, role: str) -> int:
        return int(getattr(self, f"lessons_per_day_{role}", 0))

    def max_minutes(self, role: str) -> float:
        return float(getattr(self, f"max_minutes_{role}", 20))

    def doubts_per_day(self, role: str) -> int:
        return int(getattr(self, f"doubts_per_day_{role}", 0))

    def monthly_credits(self, role: str) -> int:
        """A role's credits per month; 0 is no limit."""
        return int(getattr(self, f"monthly_credits_{role}", 0))

    def voice_price(self, provider: str) -> float:
        """USD per million characters of speech from this provider (0 for free and local voices)."""
        prices = {**VOICE_PRICES, **{k.lower(): v for k, v in self.voice_prices.items()}}
        return max(0.0, float(prices.get(provider.lower(), 0.0)))

    @cached_property
    def zone(self) -> tzinfo | None:
        """The reports' time zone; None is this computer's own (daylight saving included)."""
        name = self.timezone.strip()
        if not name or name.lower() == "local":
            return None
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            log.warning("PLATFORM_TIMEZONE=%r is not a time zone; using this computer's", name)
            return None

    @property
    def zone_name(self) -> str:
        if self.zone is not None:
            return self.timezone.strip()
        return time.tzname[time.localtime().tm_isdst > 0] or "local"


# Speech, USD per million characters (October 2026, list prices; plans differ: set PLATFORM_VOICE_PRICES).
# ElevenLabs bills about a credit per character: Creator $22 for 100k credits; Pro $99 for 500k.
VOICE_PRICES: dict[str, float] = {
    "elevenlabs": 200.0,
    "openai": 15.0,
    "google": 16.0,
    "azure": 16.0,
    "edge": 0.0,  # free
    "kokoro": 0.0,  # runs on this machine
    "http": 0.0,
    "fake": 0.0,
}
