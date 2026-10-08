"""Credits and cost (ADR-053).

Every spend is one row of an append-only ledger (`usage_ledger`):
- making lessons: each lesson's AI (exact: the tokens of its calls) and its voice (estimated: the
  characters spoken at the provider's price), posted while the lesson is made;
- learning: each doubt answer (AI exact, voice estimated) and speech input (estimated from the
  recording's size);
- adjustments: credits an admin gives back (or charges); no dollars.

Dollars are kept in millionths and credits in thousandths, as integers, so sums are exact. An entry's
credits are fixed when it is posted, at that day's price: a new `PLATFORM_CREDIT_USD` changes only
what comes after. A lesson posts the difference between what it has spent and what the ledger already
has (`Lesson.cost_billed_micros`, `Lesson.voice_chars_billed`), so nothing is counted twice, across
restarts too; a one-off entry carries a `source` key and is written once.

Periods follow the platform's clock (`PLATFORM_TIMEZONE`): today, this week (from Monday), this month
(the monthly allowance renews on the 1st), all time.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from datetime import tzinfo
from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, Integer, case, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.sql import Executable

from animator.platform.models import Lesson, UsageEntry, User, new_id
from animator.platform.settings import PlatformSettings

if TYPE_CHECKING:
    from animator.pipeline.models import Generation

# kind -> area (what the money went to)
KINDS: dict[str, str] = {
    "lesson_ai": "create",
    "lesson_voice": "create",
    "doubt_ai": "learn",
    "doubt_voice": "learn",
    "transcription": "learn",
    "adjustment": "adjustment",
}
AREAS = ("create", "learn", "adjustment")
ESTIMATED = frozenset({"lesson_voice", "doubt_voice", "transcription"})
CREDIT_MODES = ("role", "custom", "unlimited", "blocked")
PERIODS = ("today", "week", "month", "total")
BUCKET_S = 900  # 15 minutes: midnight in every time zone falls on a boundary
# A browser's recording (Opus) is about 96 kbit/s: 12 kB per second of speech. An estimate.
RECORDING_BYTES_PER_S = 12_000


def to_micros(usd: float) -> int:
    return round(usd * 1_000_000)


def usd(micros: int) -> float:
    return round(micros / 1_000_000, 6)


def credits(milli: int) -> float:
    return round(milli / 1000, 3)


def milli_of(micros: int, settings: PlatformSettings) -> int:
    """Thousandths of a credit for an amount, at today's price of a credit."""
    return round(micros / (settings.credit_usd * 1000))


def show_credits(milli: int) -> str:
    """1,000 or 12.5: for messages."""
    value = milli / 1000
    return f"{value:,.0f}" if value == int(value) else f"{value:,.1f}"


# ---------------------------------------------------------------------- the clock


@dataclass(frozen=True)
class Clock:
    """Calendar days in the platform's time zone (None: this computer's, daylight saving included)."""

    zone: tzinfo | None
    now: float

    def date(self, ts: float) -> dt.date:
        return dt.datetime.fromtimestamp(ts, self.zone).date()

    def start(self, day: dt.date) -> float:
        """When a day begins (Unix time)."""
        return dt.datetime(day.year, day.month, day.day, tzinfo=self.zone).timestamp()

    @property
    def today(self) -> dt.date:
        return self.date(self.now)

    def since(self, period: str) -> float | None:
        """Start of today, this week (Monday), this month; None for all time."""
        d = self.today
        if period == "today":
            return self.start(d)
        if period == "week":
            return self.start(d - dt.timedelta(days=d.weekday()))
        if period == "month":
            return self.start(d.replace(day=1))
        return None

    @property
    def next_month(self) -> float:
        first = self.today.replace(day=1)
        return self.start((first + dt.timedelta(days=32)).replace(day=1))


def clock(settings: PlatformSettings, now: float | None = None) -> Clock:
    return Clock(settings.zone, time.time() if now is None else now)


# ---------------------------------------------------------------------- posting


def _insert_ignore(s: AsyncSession, rows: list[dict[str, Any]]) -> Executable:
    """INSERT that skips rows whose `source` is already there (SQLite, PostgreSQL)."""
    name = s.get_bind().dialect.name
    if name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert(UsageEntry).values(rows).on_conflict_do_nothing(index_elements=["source"])
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert(UsageEntry).values(rows).on_conflict_do_nothing(index_elements=["source"])
    raise NotImplementedError(f"usage ledger: no insert-or-skip for {name}")


async def post(
    s: AsyncSession,
    settings: PlatformSettings,
    kind: str,
    user_id: str | None,
    *,
    cost_micros: int = 0,
    credits_milli: int | None = None,
    lesson_id: str | None = None,
    quantity: int = 0,
    source: str | None = None,
    note: str = "",
    actor_id: str | None = None,
    at: float | None = None,
) -> bool:
    """Add one entry to the ledger (in the caller's transaction). Nothing when it is worth nothing;
    with a `source`, once per source. True when an entry was added."""
    if kind not in KINDS:
        raise ValueError(f"unknown usage kind {kind!r}")
    milli = milli_of(cost_micros, settings) if credits_milli is None else int(credits_milli)
    if cost_micros == 0 and milli == 0:
        return False
    row: dict[str, Any] = {
        "id": new_id(),
        "user_id": user_id,
        "lesson_id": lesson_id,
        "kind": kind,
        "cost_micros": int(cost_micros),
        "credits_milli": milli,
        "estimated": kind in ESTIMATED,
        "quantity": int(quantity),
        "source": source,
        "note": note[:200],
        "actor_id": actor_id,
        "created_at": time.time() if at is None else float(at),
    }
    if source is None:
        s.add(UsageEntry(**row))
        return True
    result = await s.execute(_insert_ignore(s, [row]))
    return bool(getattr(result, "rowcount", 0))


def lesson_voice(gen: Generation) -> tuple[int, str]:
    """Characters a lesson's voice has spoken so far, and the voice's provider."""
    provider = ""
    for rec in reversed(gen.scenes):
        narration = rec.narration if isinstance(rec.narration, dict) else None
        if narration and narration.get("provider"):
            provider = str(narration["provider"])
            break
    return int(gen.metrics.tts_chars or 0), provider


async def bill_lesson(
    s: AsyncSession,
    settings: PlatformSettings,
    row: Lesson,
    gen: Generation,
    at: float,
    default_provider: str = "",
) -> int:
    """Post what a lesson has spent since its last posting (AI, voice), charged to its owner. The
    lesson row records what is posted, in the same transaction, by compare-and-set: of two posters
    (two API processes, or a restart racing a sync) only the one that moves the counter posts.
    Returns the entries added."""
    added = 0
    ai = to_micros(gen.metrics.cost_usd or 0.0)
    old = row.cost_billed_micros
    if ai != old and await _advance(s, row, "cost_billed_micros", old, ai):
        added += await post(
            s, settings, "lesson_ai", row.owner_id, cost_micros=ai - old, lesson_id=row.id, at=at
        )
    chars, provider = lesson_voice(gen)
    old = row.voice_chars_billed
    if chars != old and await _advance(s, row, "voice_chars_billed", old, chars):
        provider = provider or default_provider
        added += await post(
            s,
            settings,
            "lesson_voice",
            row.owner_id,
            # USD per million characters is micro-dollars per character.
            cost_micros=round((chars - old) * settings.voice_price(provider)),
            lesson_id=row.id,
            quantity=chars - old,
            note=provider,
            at=at,
        )
    return added


async def _advance(s: AsyncSession, row: Lesson, field: str, old: int, new: int) -> bool:
    """Move a lesson's posted-so-far counter from `old` to `new`, only if it still is `old`."""
    column = getattr(Lesson, field)
    result = await s.execute(
        update(Lesson)
        .where(Lesson.id == row.id, column == old)
        .values({field: new})
        .execution_options(synchronize_session=False)
    )
    if int(getattr(result, "rowcount", 0) or 0) != 1:
        await s.refresh(row, [field])  # someone else posted it: read what they wrote
        return False
    set_committed_value(row, field, new)
    return True


def doubt_voice(turn: dict[str, Any]) -> tuple[int, str]:
    """Characters spoken for a doubt answer (0 when it had no voice), and the provider."""
    narration = turn.get("narration")
    if not isinstance(narration, dict) or not narration.get("beats"):
        return 0, ""
    sentences = [str(x).strip() for x in turn.get("sentences") or [] if str(x).strip()]
    return len(" ".join(sentences)), str(narration.get("provider") or "")


def _number(value: Any) -> float:
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return n if n == n and abs(n) < 1e6 else 0.0  # not NaN, not absurd


async def bill_doubt(
    s: AsyncSession,
    settings: PlatformSettings,
    log_id: str,
    user_id: str | None,
    lesson_id: str,
    turn: dict[str, Any],
    at: float,
) -> int:
    """A doubt answer: its AI calls and its voice, charged to the person who asked. Once per answer."""
    added = 0
    added += await post(
        s,
        settings,
        "doubt_ai",
        user_id,
        cost_micros=to_micros(_number(turn.get("cost_usd"))),
        lesson_id=lesson_id,
        source=f"doubt:{log_id}:ai",
        at=at,
    )
    chars, provider = doubt_voice(turn)
    if chars:
        added += await post(
            s,
            settings,
            "doubt_voice",
            user_id,
            cost_micros=round(chars * settings.voice_price(provider)),
            lesson_id=lesson_id,
            quantity=chars,
            note=provider,
            source=f"doubt:{log_id}:voice",
            at=at,
        )
    return added


async def bill_transcription(
    s: AsyncSession, settings: PlatformSettings, user_id: str | None, size_bytes: int, at: float | None = None
) -> int:
    """Speech input: minutes estimated from the recording's size."""
    seconds = max(1, min(600, round(max(0, size_bytes) / RECORDING_BYTES_PER_S)))
    cost = round(seconds * settings.transcribe_usd_per_min * 1_000_000 / 60)
    return int(await post(s, settings, "transcription", user_id, cost_micros=cost, quantity=seconds, at=at))


# ---------------------------------------------------------------------- the monthly allowance


@dataclass(frozen=True)
class Allowance:
    mode: str  # role | custom | unlimited | blocked
    limit_milli: int | None  # None: no limit
    used_milli: int  # this month, net of credits given back
    period_start: float
    resets_at: float

    @property
    def remaining_milli(self) -> int | None:
        return None if self.limit_milli is None else self.limit_milli - self.used_milli

    @property
    def exhausted(self) -> bool:
        return self.limit_milli is not None and self.used_milli >= self.limit_milli

    def out(self) -> dict[str, Any]:
        remaining = self.remaining_milli
        return {
            "mode": self.mode,
            "limit": None if self.limit_milli is None else credits(self.limit_milli),
            "used": credits(self.used_milli),
            "remaining": None if remaining is None else credits(remaining),
            "unlimited": self.limit_milli is None,
            "blocked": self.mode == "blocked",
            "exhausted": self.exhausted,
            "period_start": self.period_start,
            "resets_at": self.resets_at,
        }


def limit_milli(settings: PlatformSettings, role: str, mode: str, limit: int) -> int | None:
    """A person's monthly credits in thousandths; None is no limit."""
    if mode == "unlimited":
        return None
    if mode == "blocked":
        return 0
    if mode == "custom":
        return max(0, int(limit)) * 1000
    per_role = settings.monthly_credits(role)
    return per_role * 1000 if per_role > 0 else None


async def used_since(s: AsyncSession, user_id: str, since: float) -> int:
    used = await s.scalar(
        select(func.coalesce(func.sum(UsageEntry.credits_milli), 0)).where(
            UsageEntry.user_id == user_id, UsageEntry.created_at >= since
        )
    )
    return int(used or 0)


async def allowance(
    s: AsyncSession, settings: PlatformSettings, user: User, now: float | None = None
) -> Allowance:
    ck = clock(settings, now)
    start = ck.since("month")
    assert start is not None
    return Allowance(
        mode=user.credit_mode if user.credit_mode in CREDIT_MODES else "role",
        limit_milli=limit_milli(settings, user.role, user.credit_mode, user.credit_limit),
        used_milli=await used_since(s, user.id, start),
        period_start=start,
        resets_at=ck.next_month,
    )


def refusal(a: Allowance, settings: PlatformSettings) -> str | None:
    """Why a person may not start something that spends now (None: they may)."""
    if a.mode == "blocked":
        return "Your account cannot use credits at the moment. Contact support if this is unexpected."
    if a.exhausted and a.limit_milli is not None:
        renew = dt.datetime.fromtimestamp(a.resets_at, settings.zone)
        return (
            f"You have used this month's {show_credits(a.limit_milli)} credits. "
            f"They renew on {renew.day} {renew:%B}."
        )
    return None


async def check(s: AsyncSession, settings: PlatformSettings, user_id: str) -> str | None:
    """The refusal for a person by id (None when they may spend, or when the account is gone)."""
    user = await s.get(User, user_id)
    if user is None:
        return None
    return refusal(await allowance(s, settings, user), settings)


# ---------------------------------------------------------------------- reports


def bucket(s: AsyncSession, column: Any) -> ColumnElement[Any]:
    """15-minute buckets of a Unix time (whole numbers, rounded down)."""
    if s.get_bind().dialect.name == "sqlite":
        return cast(column / BUCKET_S, Integer)  # truncates: right for times after 1970
    return func.floor(column / BUCKET_S)


Sums = dict[str, tuple[int, int, int]]  # kind -> (micro-dollars, milli-credits, entries)


async def period_sums(s: AsyncSession, ck: Clock, *where: Any) -> dict[str, Sums]:
    """Per kind, for today, this week, this month and all time, in one pass over the ledger."""
    cols: list[Any] = []
    for period in PERIODS[:3]:
        start = ck.since(period)
        inside = UsageEntry.created_at >= start
        cols += [
            func.sum(case((inside, UsageEntry.cost_micros), else_=0)),
            func.sum(case((inside, UsageEntry.credits_milli), else_=0)),
            func.sum(case((inside, 1), else_=0)),
        ]
    cols += [func.sum(UsageEntry.cost_micros), func.sum(UsageEntry.credits_milli), func.count()]
    rows = (await s.execute(select(UsageEntry.kind, *cols).where(*where).group_by(UsageEntry.kind))).all()
    out: dict[str, Sums] = {p: {} for p in PERIODS}
    for row in rows:
        values = [int(v or 0) for v in row[1:]]
        for i, period in enumerate(PERIODS):
            out[period][str(row[0])] = (values[3 * i], values[3 * i + 1], values[3 * i + 2])
    return out


def shape(sums: Sums) -> dict[str, Any]:
    """A period's totals: dollars, credits, by kind and by area, and how much of it is estimated."""
    cost = sum(v[0] for v in sums.values())
    milli = sum(v[1] for v in sums.values())
    by_area = {a: [0, 0] for a in AREAS}
    for kind, (c, m, _) in sums.items():
        area = by_area.setdefault(KINDS.get(kind, "learn"), [0, 0])
        area[0] += c
        area[1] += m
    return {
        "cost_usd": usd(cost),
        "credits": credits(milli),
        "entries": sum(v[2] for v in sums.values()),
        "estimated_usd": usd(sum(c for k, (c, _, _) in sums.items() if k in ESTIMATED)),
        "by_kind": {
            k: {"cost_usd": usd(v[0]), "credits": credits(v[1]), "count": v[2]}
            for k in KINDS
            if (v := sums.get(k)) is not None
        },
        "by_area": {a: {"cost_usd": usd(c), "credits": credits(m)} for a, (c, m) in by_area.items()},
    }


async def daily(s: AsyncSession, ck: Clock, days: int, *where: Any) -> list[dict[str, Any]]:
    """The last `days` calendar days (oldest first): dollars and credits, making lessons vs learning."""
    days = max(1, min(days, 366))
    first = ck.today - dt.timedelta(days=days - 1)
    since = ck.start(first)
    b = bucket(s, UsageEntry.created_at)
    rows = (
        await s.execute(
            select(b, UsageEntry.kind, func.sum(UsageEntry.cost_micros), func.sum(UsageEntry.credits_milli))
            .where(UsageEntry.created_at >= since, *where)
            .group_by(b, UsageEntry.kind)
        )
    ).all()
    acc: dict[str, list[int]] = {
        (first + dt.timedelta(days=i)).isoformat(): [0, 0, 0, 0] for i in range(days)
    }
    for slot, kind, cost, milli in rows:
        day = ck.date(int(slot) * BUCKET_S).isoformat()
        cell = acc.get(day)
        if cell is None:
            continue
        cell[0] += int(cost or 0)
        cell[1] += int(milli or 0)
        area = KINDS.get(str(kind), "learn")
        if area == "create":
            cell[2] += int(cost or 0)
        elif area == "learn":
            cell[3] += int(cost or 0)
    return [
        {
            "day": day,
            "cost_usd": usd(c),
            "credits": credits(m),
            "create_usd": usd(cr),
            "learn_usd": usd(le),
        }
        for day, (c, m, cr, le) in acc.items()
    ]
