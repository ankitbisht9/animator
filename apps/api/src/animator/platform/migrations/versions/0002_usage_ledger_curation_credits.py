"""usage ledger, curation, credits (ADR-053)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08 13:47:43.494792

Existing tables only get new columns and indexes (plain ALTER TABLE ADD COLUMN, never a batch copy:
copying `lessons` would cascade-delete its progress, notes and ratings). Lessons public today stay in
Explore: they are marked listed by their owner.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_ledger",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=True),
        sa.Column("lesson_id", sa.String(length=16), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("cost_micros", sa.Integer(), nullable=False),
        sa.Column("credits_milli", sa.Integer(), nullable=False),
        sa.Column("estimated", sa.Boolean(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=False),
        sa.Column("actor_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source"),
    )
    op.create_index("ix_usage_ledger_created_at", "usage_ledger", ["created_at"], unique=False)
    op.create_index("ix_usage_ledger_lesson_id", "usage_ledger", ["lesson_id"], unique=False)
    op.create_index("ix_usage_user_created", "usage_ledger", ["user_id", "created_at"], unique=False)

    op.create_table(
        "lesson_curation",
        sa.Column("lesson_id", sa.String(length=16), nullable=False),
        sa.Column("pin", sa.String(length=8), nullable=False),
        sa.Column("pin_note", sa.String(length=300), nullable=False),
        sa.Column("pinned_by", sa.String(length=32), nullable=True),
        sa.Column("pinned_at", sa.Float(), nullable=True),
        sa.Column("review", sa.String(length=12), nullable=False),
        sa.Column("review_note", sa.String(length=500), nullable=False),
        sa.Column("reviewed_by", sa.String(length=32), nullable=True),
        sa.Column("reviewed_at", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pinned_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("lesson_id"),
    )
    op.create_index("ix_lesson_curation_pin", "lesson_curation", ["pin"], unique=False)
    op.create_index("ix_lesson_curation_review", "lesson_curation", ["review"], unique=False)

    op.add_column(
        "lessons", sa.Column("cost_billed_micros", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "lessons", sa.Column("voice_chars_billed", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column("lessons", sa.Column("explore_listed", sa.Boolean(), server_default="0", nullable=False))
    op.add_column(
        "lessons", sa.Column("explore_source", sa.String(length=8), server_default="", nullable=False)
    )
    op.add_column("lessons", sa.Column("explore_at", sa.Float(), nullable=True))
    op.add_column("lessons", sa.Column("explore_first_at", sa.Float(), nullable=True))
    op.add_column("lessons", sa.Column("explore_opt_out", sa.Boolean(), server_default="0", nullable=False))
    op.create_index("ix_lessons_explore_subject", "lessons", ["explore_listed", "subject"], unique=False)
    op.create_index("ix_lessons_rating_count", "lessons", ["rating_count"], unique=False)

    op.create_index("ix_ratings_lesson_updated", "ratings", ["lesson_id", "updated_at"], unique=False)

    op.add_column(
        "users", sa.Column("credit_mode", sa.String(length=12), server_default="role", nullable=False)
    )
    op.add_column("users", sa.Column("credit_limit", sa.Integer(), server_default="0", nullable=False))

    # Explore listed public lessons before: they stay, listed by their owner.
    lessons = sa.table(
        "lessons",
        sa.column("visibility", sa.String),
        sa.column("status", sa.String),
        sa.column("deleted_at", sa.Float),
        sa.column("completed_at", sa.Float),
        sa.column("created_at", sa.Float),
        sa.column("explore_listed", sa.Boolean),
        sa.column("explore_source", sa.String),
        sa.column("explore_at", sa.Float),
        sa.column("explore_first_at", sa.Float),
    )
    since = sa.func.coalesce(lessons.c.completed_at, lessons.c.created_at)
    op.execute(
        lessons.update()
        .where(
            lessons.c.visibility == "public",
            lessons.c.status == "complete",
            lessons.c.deleted_at.is_(None),
        )
        .values(explore_listed=True, explore_source="owner", explore_at=since, explore_first_at=since)
    )


def downgrade() -> None:
    # Native DROP COLUMN (SQLite 3.35+): no table copy, so nothing cascades.
    op.drop_column("users", "credit_limit")
    op.drop_column("users", "credit_mode")
    op.drop_index("ix_ratings_lesson_updated", table_name="ratings")
    op.drop_index("ix_lessons_rating_count", table_name="lessons")
    op.drop_index("ix_lessons_explore_subject", table_name="lessons")
    for column in (
        "explore_opt_out",
        "explore_first_at",
        "explore_at",
        "explore_source",
        "explore_listed",
        "voice_chars_billed",
        "cost_billed_micros",
    ):
        op.drop_column("lessons", column)
    op.drop_index("ix_lesson_curation_review", table_name="lesson_curation")
    op.drop_index("ix_lesson_curation_pin", table_name="lesson_curation")
    op.drop_table("lesson_curation")
    op.drop_index("ix_usage_user_created", table_name="usage_ledger")
    op.drop_index("ix_usage_ledger_lesson_id", table_name="usage_ledger")
    op.drop_index("ix_usage_ledger_created_at", table_name="usage_ledger")
    op.drop_table("usage_ledger")
