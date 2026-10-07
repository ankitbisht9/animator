"""Alembic environment: migrations run on a connection the platform hands over
(`Database.migrate`, or `python -m animator.platform migrate|revision`)."""

from __future__ import annotations

from alembic import context

from animator.platform.models import Base

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("run migrations through animator.platform: python -m animator.platform migrate")

context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    render_as_batch=True,  # SQLite cannot ALTER most things: batch mode copies the table
    compare_type=True,
)
with context.begin_transaction():
    context.run_migrations()
