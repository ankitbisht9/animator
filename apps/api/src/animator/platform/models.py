"""The platform's tables (ADR-051). Times are Unix seconds (as the lessons' own records); ids are
random hex. Lessons stay in their files (the pipeline's store): `lessons` here is the catalog that
says whose a lesson is, who may see it, and what the platform shows about it."""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True)  # always lower case
    name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="learner", index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | suspended
    email_verified_at: Mapped[float | None] = mapped_column(Float, default=None)
    bio: Mapped[str] = mapped_column(String(300), default="")
    avatar: Mapped[str] = mapped_column(String(16), default="")
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[float | None] = mapped_column(Float, default=None)
    created_at: Mapped[float] = mapped_column(Float, default=now, index=True)
    updated_at: Mapped[float] = mapped_column(Float, default=now, onupdate=now)
    last_login_at: Mapped[float | None] = mapped_column(Float, default=None)
    # Credits per month (ADR-053): role (the role's allowance) | custom (credit_limit) | unlimited |
    # blocked (may watch, not spend).
    credit_mode: Mapped[str] = mapped_column(String(12), default="role", server_default="role")
    credit_limit: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class AuthSession(Base):
    """A signed-in device. The refresh token is kept only as a hash and changes at every refresh;
    the previous hash is kept to notice a stolen token being replayed (then the session ends)."""

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    previous_hash: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    remember: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    last_used_at: Mapped[float] = mapped_column(Float, default=now)
    expires_at: Mapped[float] = mapped_column(Float)
    revoked_at: Mapped[float | None] = mapped_column(Float, default=None)
    revoked_reason: Mapped[str | None] = mapped_column(String(40), default=None)


class OneTimeToken(Base):
    """Email verification and password reset links (stored as hashes, used once)."""

    __tablename__ = "one_time_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(24))  # verify_email | reset_password
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    expires_at: Mapped[float] = mapped_column(Float)
    used_at: Mapped[float | None] = mapped_column(Float, default=None)

    __table_args__ = (Index("ix_tokens_user_purpose", "user_id", "purpose"),)


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # the generation id, g_<12 hex>
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    title: Mapped[str] = mapped_column(String(200), default="")
    subtitle: Mapped[str] = mapped_column(String(400), default="")
    topic: Mapped[str] = mapped_column(String(500))
    audience: Mapped[str] = mapped_column(String(120), default="")
    language: Mapped[str] = mapped_column(String(40), default="English")
    duration_min: Mapped[float] = mapped_column(Float, default=3)
    planned_s: Mapped[float] = mapped_column(Float, default=0)
    scene_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    subject: Mapped[str] = mapped_column(String(32), default="general", index=True)
    visibility: Mapped[str] = mapped_column(String(12), default="private")  # private | unlisted | public
    featured: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    views: Mapped[int] = mapped_column(Integer, default=0)
    rating_sum: Mapped[int] = mapped_column(Integer, default=0)
    rating_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)
    completed_at: Mapped[float | None] = mapped_column(Float, default=None)
    deleted_at: Mapped[float | None] = mapped_column(Float, default=None)
    # What of the lesson's spend is already in the usage ledger (ADR-053): AI in millionths of a
    # dollar, voice in characters. The ledger gets the difference, so nothing is counted twice.
    cost_billed_micros: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    voice_chars_billed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Explore (ADR-053). Derived by curation.decide() and stored, so Explore is one indexed query.
    # source: owner (published) | auto (its ratings) | admin (added by an admin).
    explore_listed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    explore_source: Mapped[str] = mapped_column(String(8), default="", server_default="")
    explore_at: Mapped[float | None] = mapped_column(Float, default=None)  # listed since
    explore_first_at: Mapped[float | None] = mapped_column(Float, default=None)  # first listed
    explore_opt_out: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")  # owner

    __table_args__ = (
        Index("ix_lessons_owner_created", "owner_id", "created_at"),
        Index("ix_lessons_visibility_created", "visibility", "created_at"),
        Index("ix_lessons_explore_subject", "explore_listed", "subject"),
        Index("ix_lessons_rating_count", "rating_count"),
    )


class Curation(Base):
    """What curators decided about a lesson (ADR-053): kept in or out of Explore whatever its ratings
    and sharing say (pin), and the team's review of a lesson that needs work."""

    __tablename__ = "lesson_curation"

    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True)
    pin: Mapped[str] = mapped_column(String(8), default="", index=True)  # "" | include | exclude
    pin_note: Mapped[str] = mapped_column(String(300), default="")
    pinned_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    pinned_at: Mapped[float | None] = mapped_column(Float, default=None)
    review: Mapped[str] = mapped_column(
        String(12), default="", index=True
    )  # "" | flagged | working | resolved
    review_note: Mapped[str] = mapped_column(String(500), default="")
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    reviewed_at: Mapped[float | None] = mapped_column(Float, default=None)
    updated_at: Mapped[float] = mapped_column(Float, default=now, onupdate=now)


class UsageEntry(Base):
    """One spend, append-only (ADR-053): what it cost in dollars (millionths, exact sums) and in
    credits (thousandths) at the price of its day. Corrections are new entries, never edits.

    kind: lesson_ai | lesson_voice (making lessons), doubt_ai | doubt_voice | transcription (learning),
    adjustment (credits an admin gave back or charged; no dollars). `source` makes a one-off entry
    idempotent (a doubt answer is recorded once however often it is seen)."""

    __tablename__ = "usage_ledger"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    lesson_id: Mapped[str | None] = mapped_column(String(16), default=None, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    cost_micros: Mapped[int] = mapped_column(Integer, default=0)
    credits_milli: Mapped[int] = mapped_column(Integer, default=0)
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0)  # characters, seconds of audio
    source: Mapped[str | None] = mapped_column(String(80), unique=True, default=None)
    note: Mapped[str] = mapped_column(String(200), default="")
    actor_id: Mapped[str | None] = mapped_column(String(32), default=None)  # who made an adjustment
    created_at: Mapped[float] = mapped_column(Float, default=now, index=True)

    __table_args__ = (Index("ix_usage_user_created", "user_id", "created_at"),)


class Progress(Base):
    __tablename__ = "progress"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True)
    position_s: Mapped[float] = mapped_column(Float, default=0)
    duration_s: Mapped[float] = mapped_column(Float, default=0)
    percent: Mapped[float] = mapped_column(Float, default=0)
    watched_s: Mapped[float] = mapped_column(Float, default=0)
    started_at: Mapped[float] = mapped_column(Float, default=now)
    last_watched_at: Mapped[float] = mapped_column(Float, default=now)
    completed_at: Mapped[float | None] = mapped_column(Float, default=None)

    __table_args__ = (Index("ix_progress_user_watched", "user_id", "last_watched_at"),)


class ActivityDay(Base):
    """Seconds learned per day (the learner's own calendar day), for streaks and charts."""

    __tablename__ = "activity_days"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD
    seconds: Mapped[float] = mapped_column(Float, default=0)


class Saved(Base):
    __tablename__ = "saved"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)

    __table_args__ = (Index("ix_saved_user_created", "user_id", "created_at"),)


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    t_s: Mapped[float] = mapped_column(Float, default=0)
    text: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now, onupdate=now)

    __table_args__ = (Index("ix_notes_user_lesson", "user_id", "lesson_id", "t_s"),)


class Rating(Base):
    __tablename__ = "ratings"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True)
    stars: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now, onupdate=now)

    __table_args__ = (Index("ix_ratings_lesson_updated", "lesson_id", "updated_at"),)


class DoubtLog(Base):
    """A doubt asked (ADR-050), as the gateway saw it go by: who asked, on which lesson, what."""

    __tablename__ = "doubt_log"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), default=None)
    lesson_id: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str] = mapped_column(String(16), index=True)
    turn: Mapped[int] = mapped_column(Integer, default=1)
    question: Mapped[str] = mapped_column(String(1000))
    kind: Mapped[str] = mapped_column(String(16), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=now)

    __table_args__ = (Index("ix_doubts_user_created", "user_id", "created_at"),)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(500), default="")
    link: Mapped[str] = mapped_column(String(300), default="")
    read_at: Mapped[float | None] = mapped_column(Float, default=None)
    created_at: Mapped[float] = mapped_column(Float, default=now)

    __table_args__ = (Index("ix_notifications_user_created", "user_id", "created_at"),)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    email: Mapped[str] = mapped_column(String(254))
    subject: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | pending | resolved | closed
    priority: Mapped[str] = mapped_column(String(8), default="normal")
    lesson_id: Mapped[str | None] = mapped_column(String(16), default=None)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)

    __table_args__ = (
        Index("ix_tickets_status_updated", "status", "updated_at"),
        Index("ix_tickets_user_updated", "user_id", "updated_at"),
    )


class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"))
    author_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    staff: Mapped[bool] = mapped_column(Boolean, default=False)
    body: Mapped[str] = mapped_column(String(5000))
    created_at: Mapped[float] = mapped_column(Float, default=now)

    __table_args__ = (Index("ix_ticket_messages_ticket", "ticket_id", "created_at"),)


class AuditEvent(Base):
    """Security and administration events (kept when the actor's account is gone)."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    actor_id: Mapped[str | None] = mapped_column(String(32), default=None)
    action: Mapped[str] = mapped_column(String(48), index=True)
    target: Mapped[str] = mapped_column(String(64), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=now, index=True)

    __table_args__ = (Index("ix_audit_actor_created", "actor_id", "created_at"),)
