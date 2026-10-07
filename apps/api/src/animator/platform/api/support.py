"""/api/v1/support: tickets (a subject, a category and a conversation with the support team).
Learners see their own; staff (support:manage) see and answer all, and set status and priority."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from animator.platform.deps import DB, Me, Plat, Staff, client_ip
from animator.platform.models import Ticket, TicketMessage, User
from animator.platform.rbac import Perm
from animator.platform.schemas import MessageIn, TicketIn, TicketPatchIn

router = APIRouter(prefix="/api/v1", tags=["support"])


def ticket_out(t: Ticket, last: str | None = None, name: str | None = None) -> dict[str, Any]:
    return {
        "id": t.id,
        "ref": f"#{t.id[:6].upper()}",
        "subject": t.subject,
        "category": t.category,
        "status": t.status,
        "priority": t.priority,
        "lesson_id": t.lesson_id,
        "email": t.email,
        "name": name,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "last_message": last,
    }


async def _ticket(s: DB, ticket_id: str, p: Me) -> Ticket:
    t = await s.get(Ticket, ticket_id)
    if t is None or (t.user_id != p.user_id and not p.can(Perm.SUPPORT_MANAGE)):
        raise HTTPException(404, "No such ticket.")
    return t


@router.post("/support/tickets", status_code=201)
async def open_ticket(body: TicketIn, request: Request, p: Me, platform: Plat, s: DB) -> dict[str, Any]:
    if platform.limiter.hit(f"ticket:{p.user_id}", 5, 3600) is not None:
        raise HTTPException(429, "You opened several tickets just now. We will answer them soon.")
    t = Ticket(
        user_id=p.user_id,
        email=p.email,
        subject=body.subject,
        category=body.category,
        lesson_id=body.lesson_id,
    )
    s.add(t)
    await s.flush()
    s.add(TicketMessage(ticket_id=t.id, author_id=p.user_id, staff=False, body=body.message))
    await s.commit()
    await platform.audit("ticket.open", p.user_id, t.id, client_ip(request), category=body.category)
    staff = (
        await s.scalars(select(User.id).where(User.role == "admin", User.status == "active").limit(20))
    ).all()
    for uid in staff:
        if uid != p.user_id:
            await platform.notify(
                uid, "ticket_new", f"New ticket: {body.subject}", p.name, f"/admin/support/{t.id}"
            )
    return ticket_out(t, body.message[:160], p.name)


@router.get("/support/tickets")
async def my_tickets(p: Me, s: DB) -> list[dict[str, Any]]:
    rows = (
        await s.scalars(
            select(Ticket).where(Ticket.user_id == p.user_id).order_by(Ticket.updated_at.desc()).limit(100)
        )
    ).all()
    return [ticket_out(t) for t in rows]


@router.get("/support/tickets/{ticket_id}")
async def ticket(ticket_id: str, p: Me, s: DB) -> dict[str, Any]:
    t = await _ticket(s, ticket_id, p)
    msgs = (
        await s.execute(
            select(TicketMessage, User.name)
            .outerjoin(User, User.id == TicketMessage.author_id)
            .where(TicketMessage.ticket_id == t.id)
            .order_by(TicketMessage.created_at)
        )
    ).all()
    owner = await s.get(User, t.user_id) if t.user_id else None
    out = ticket_out(t, name=owner.name if owner else None)
    out["messages"] = [
        {
            "id": m.id,
            "staff": m.staff,
            "author": (name or "Support team") if m.staff else (name or "You"),
            "mine": m.author_id == p.user_id,
            "body": m.body,
            "created_at": m.created_at,
        }
        for m, name in msgs
    ]
    return out


@router.post("/support/tickets/{ticket_id}/messages", status_code=201)
async def reply(ticket_id: str, body: MessageIn, p: Me, platform: Plat, s: DB) -> dict[str, Any]:
    t = await _ticket(s, ticket_id, p)
    if platform.limiter.hit(f"reply:{p.user_id}", 60, 3600) is not None:
        raise HTTPException(429, "Too many messages. Slow down a little.")
    staff = p.can(Perm.SUPPORT_MANAGE) and t.user_id != p.user_id
    if t.status == "closed" and not staff:
        t.status = "open"  # the learner reopens it by writing
    s.add(TicketMessage(ticket_id=t.id, author_id=p.user_id, staff=staff, body=body.body))
    t.updated_at = time.time()
    if staff and t.status == "open":
        t.status = "pending"  # waiting for the learner
    elif not staff and t.status in ("pending", "resolved"):
        t.status = "open"
    await s.commit()
    if staff and t.user_id:
        await platform.notify(
            t.user_id, "ticket_reply", f"Support replied: {t.subject}", body.body[:160], f"/support/{t.id}"
        )
    return await ticket(t.id, p, s)


@router.post("/support/tickets/{ticket_id}/close")
async def close(ticket_id: str, p: Me, s: DB) -> dict[str, Any]:
    t = await _ticket(s, ticket_id, p)
    t.status = "closed"
    t.updated_at = time.time()
    await s.commit()
    return ticket_out(t)


# ---------------------------------------------------------------------- staff


@router.get("/admin/tickets")
async def queue(
    p: Staff, s: DB, status: str = "active", q: str | None = None, offset: int = 0
) -> dict[str, Any]:
    stmt = select(Ticket, User.name).outerjoin(User, User.id == Ticket.user_id)
    if status == "active":
        stmt = stmt.where(Ticket.status.in_(("open", "pending")))
    elif status in ("open", "pending", "resolved", "closed"):
        stmt = stmt.where(Ticket.status == status)
    if q and q.strip():
        term = f"%{q.strip()[:80].lower()}%"
        stmt = stmt.where(func.lower(Ticket.subject).like(term) | func.lower(Ticket.email).like(term))
    total = await s.scalar(select(func.count()).select_from(stmt.subquery()))
    prio = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    rows = (await s.execute(stmt.order_by(Ticket.updated_at.desc()).offset(max(0, offset)).limit(50))).all()
    items = sorted((ticket_out(t, name=n) for t, n in rows), key=lambda x: (prio.get(str(x["priority"]), 2),))
    return {"items": items, "total": int(total or 0)}


@router.patch("/admin/tickets/{ticket_id}")
async def triage(
    ticket_id: str, body: TicketPatchIn, request: Request, p: Staff, platform: Plat, s: DB
) -> dict[str, Any]:
    t = await s.get(Ticket, ticket_id)
    if t is None:
        raise HTTPException(404, "No such ticket.")
    if body.status is not None:
        t.status = body.status
    if body.priority is not None:
        t.priority = body.priority
    t.updated_at = time.time()
    await s.commit()
    await platform.audit(
        "ticket.triage", p.user_id, t.id, client_ip(request), **body.model_dump(exclude_none=True)
    )
    if body.status == "resolved" and t.user_id:
        await platform.notify(t.user_id, "ticket_resolved", f"Resolved: {t.subject}", "", f"/support/{t.id}")
    return ticket_out(t)
