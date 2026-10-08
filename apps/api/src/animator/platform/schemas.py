"""Request and response shapes of /api/v1. Inputs forbid unknown fields and bound every length."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

Visibility = Literal["private", "unlisted", "public"]
Theme = Literal["light", "dark", "system"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------- auth


class RegisterIn(_In):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    remember: bool = True


class LoginIn(_In):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    remember: bool = True


class ForgotIn(_In):
    email: EmailStr


class ResetIn(_In):
    token: str = Field(min_length=16, max_length=200)
    password: str = Field(min_length=1, max_length=256)


class TokenIn(_In):
    token: str = Field(min_length=16, max_length=200)


# ---------------------------------------------------------------------- account


class Preferences(_In):
    """What the learner chose (onboarding and settings). Every field is optional: a PATCH merges."""

    language: Literal["English", "Hinglish"] | None = None
    audience: str | None = Field(default=None, max_length=120)
    duration_min: float | None = Field(default=None, ge=0.5, le=20)
    layout: Literal["classic", "planned"] | None = None
    interests: list[str] | None = Field(default=None, max_length=12)
    weekly_goal_min: int | None = Field(default=None, ge=10, le=1200)
    playback_rate: float | None = Field(default=None, ge=0.5, le=2)
    captions: bool | None = None
    voice: bool | None = None
    autoplay: bool | None = None
    theme: Theme | None = None
    reduced_motion: bool | None = None
    notify_lesson_ready: bool | None = None
    notify_support: bool | None = None
    notify_weekly_summary: bool | None = None

    @field_validator("interests")
    @classmethod
    def _interests(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        from animator.platform.catalog import SUBJECTS

        return [x for x in dict.fromkeys(v) if x in SUBJECTS]


class ProfileIn(_In):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    bio: str | None = Field(default=None, max_length=300)
    avatar: str | None = Field(default=None, pattern=r"^[a-z0-9-]{0,16}$")
    preferences: Preferences | None = None
    onboarded: bool | None = None


class PasswordIn(_In):
    current: str = Field(min_length=1, max_length=256)
    new: str = Field(min_length=1, max_length=256)


class DeleteAccountIn(_In):
    password: str = Field(min_length=1, max_length=256)
    confirm: Literal["DELETE"]


# ---------------------------------------------------------------------- lessons


class LessonCreateIn(_In):
    topic: str = Field(min_length=3, max_length=500)
    audience: str = Field(default="high-school student", max_length=120)
    duration_min: float = Field(default=3.0, ge=0.5, le=20.0)
    style: str | None = Field(default=None, max_length=300)
    language: str = Field(default="English", max_length=40)
    paced: bool = False
    layout: Literal["classic", "planned"] | None = None
    visibility: Visibility = "private"
    subject: str | None = Field(default=None, max_length=32)


class LessonPatchIn(_In):
    visibility: Visibility | None = None
    subject: str | None = Field(default=None, max_length=32)
    featured: bool | None = None  # admins
    # The owner: do not list it in Explore because of its ratings (ADR-053).
    explore_opt_out: bool | None = None


class ProgressIn(_In):
    position_s: float = Field(ge=0, le=36_000)
    duration_s: float = Field(ge=0, le=36_000)
    # Seconds actually watched since the last report (clamped by the server).
    delta_s: float = Field(default=0, ge=0, le=600)
    # The learner's own calendar day (YYYY-MM-DD), for streaks in their time zone.
    day: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class NoteIn(_In):
    t_s: float = Field(ge=0, le=36_000)
    text: str = Field(min_length=1, max_length=2000)


class NotePatchIn(_In):
    text: str = Field(min_length=1, max_length=2000)


class RatingIn(_In):
    stars: int = Field(ge=1, le=5)
    # None keeps the comment already given (changing the stars does not erase it).
    comment: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------------- support


class TicketIn(_In):
    subject: str = Field(min_length=3, max_length=200)
    category: Literal["technical", "lesson", "account", "billing", "feedback", "other"]
    message: str = Field(min_length=10, max_length=5000)
    lesson_id: str | None = Field(default=None, pattern=r"^g_[0-9a-f]{12}$")


class MessageIn(_In):
    body: str = Field(min_length=1, max_length=5000)


class TicketPatchIn(_In):
    status: Literal["open", "pending", "resolved", "closed"] | None = None
    priority: Literal["low", "normal", "high", "urgent"] | None = None


# ---------------------------------------------------------------------- admin


class UserPatchIn(_In):
    role: Literal["learner", "educator", "admin"] | None = None
    status: Literal["active", "suspended"] | None = None


class AdminLessonPatchIn(_In):
    visibility: Visibility | None = None
    featured: bool | None = None
    deleted: bool | None = None
    subject: str | None = Field(default=None, max_length=32)


# ---------------------------------------------------------------------- credits and Explore (ADR-053)


class CreditsIn(_In):
    """A person's monthly credits: their role's, their own number, no limit, or none (may watch
    and learn from what exists, not start anything that costs)."""

    mode: Literal["role", "custom", "unlimited", "blocked"]
    limit: int | None = Field(default=None, ge=1, le=10_000_000)

    @model_validator(mode="after")
    def _limit(self) -> CreditsIn:
        if self.mode == "custom" and self.limit is None:
            raise ValueError("Give the number of credits per month.")
        if self.mode != "custom":
            self.limit = None
        return self


class AdjustmentIn(_In):
    """Credits given back to a person (positive) or charged (negative), with the reason."""

    credits: float = Field(ge=-1_000_000, le=1_000_000)
    note: str = Field(min_length=3, max_length=200)

    @field_validator("credits")
    @classmethod
    def _nonzero(cls, v: float) -> float:
        if round(v * 1000) == 0:
            raise ValueError("Give a number of credits other than 0.")
        return v


class ExploreIn(_In):
    """An admin's decision about a lesson in Explore. include: listed whatever its ratings say;
    exclude: never listed; automatic: back to the rule. `share` confirms that a private lesson becomes
    shared with a link (people must be able to open what Explore shows)."""

    action: Literal["include", "exclude", "automatic"]
    note: str = Field(default="", max_length=300)
    share: bool = False
    subject: str | None = Field(default=None, max_length=32)


class ReviewIn(_In):
    """The team's follow-up on a lesson: flagged (needs work), working (someone is on it), resolved,
    or "" (no review)."""

    status: Literal["", "flagged", "working", "resolved"]
    note: str = Field(default="", max_length=500)


class Page(BaseModel):
    items: list[dict[str, Any]]
    total: int | None = None
    has_more: bool = False
