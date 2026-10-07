"""Platform administration from the command line.

    uv run python -m animator.platform migrate
    uv run python -m animator.platform create-admin --email you@example.org --name "Your Name"
    uv run python -m animator.platform set-role someone@example.org educator
    uv run python -m animator.platform revision -m "what changed"     (a new migration, for developers)

create-admin asks for the password (it never appears on the command line or in the shell history).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from alembic import command
from sqlalchemy import Connection, select

from animator.platform.accounts import create_user
from animator.platform.core import Platform
from animator.platform.db import alembic_config
from animator.platform.models import Lesson, User
from animator.platform.security import password_problem
from animator.platform.settings import PlatformSettings


async def _migrate(platform: Platform) -> None:
    await platform.db.migrate()
    print(f"database is up to date: {platform.db.url.split('///')[-1]}")


async def _revision(platform: Platform, message: str) -> None:
    def rev(conn: Connection) -> None:
        command.revision(alembic_config(conn), message=message, autogenerate=True)

    async with platform.db.engine.begin() as conn:
        await conn.run_sync(rev)


async def _create_admin(platform: Platform, email: str, name: str) -> int:
    await platform.db.migrate()
    email = email.strip().lower()
    password = getpass.getpass("Password for the admin: ")
    problem = password_problem(password, email, name, platform.settings.password_min_length)
    if problem:
        print(f"refused: {problem}", file=sys.stderr)
        return 2
    if getpass.getpass("Repeat it: ") != password:
        print("refused: the passwords differ", file=sys.stderr)
        return 2
    async with platform.db.session() as s:
        user = await s.scalar(select(User).where(User.email == email))
        if user is not None:
            user.role = "admin"
            print(f"{email} already had an account: it is now an admin (password unchanged)")
        else:
            user = await create_user(s, platform, email, password, name, role="admin")
            import time

            user.email_verified_at = time.time()
            print(f"admin created: {email}")
        await s.commit()
        orphans = (await s.scalars(select(Lesson).where(Lesson.owner_id.is_(None)))).all()
        for row in orphans:
            row.owner_id = user.id
        await s.commit()
        if orphans:
            print(f"{len(orphans)} lesson(s) made before accounts now belong to {email}")
    return 0


async def _set_role(platform: Platform, email: str, role: str) -> int:
    await platform.db.migrate()
    async with platform.db.session() as s:
        user = await s.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            print(f"no account for {email}", file=sys.stderr)
            return 1
        user.role = role
        await s.commit()
    print(f"{email} is now {role}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m animator.platform")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    rev = sub.add_parser("revision")
    rev.add_argument("-m", "--message", required=True)
    ca = sub.add_parser("create-admin")
    ca.add_argument("--email", required=True)
    ca.add_argument("--name", default="Admin")
    sr = sub.add_parser("set-role")
    sr.add_argument("email")
    sr.add_argument("role", choices=["learner", "educator", "admin"])
    args = parser.parse_args()

    async def run() -> int:
        platform = Platform(PlatformSettings())
        try:
            if args.cmd == "migrate":
                await _migrate(platform)
                return 0
            if args.cmd == "revision":
                await _revision(platform, args.message)
                return 0
            if args.cmd == "create-admin":
                return await _create_admin(platform, args.email, args.name)
            return await _set_role(platform, args.email, args.role)
        finally:
            await platform.db.close()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
