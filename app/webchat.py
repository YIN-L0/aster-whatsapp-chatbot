"""WhatsApp-style web chat for testing and demos without a Meta account.

Uses the same pipeline as WhatsApp: the browser posts an inbound message, the reply is
generated in the background and persisted by app.outbound, and the page polls for it.
"""

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.channels import WEB_PREFIX, InboundMessage
from app.config import get_settings
from app.db import session_scope
from app.models import Handoff, Message, User
from app.pipeline import ingest, process_user

router = APIRouter(prefix="/chat")
PAGE = Path(__file__).parent / "static" / "chat.html"
_WEB_ID = re.compile(rf"^{WEB_PREFIX}\d{{6,20}}$")


def _check(key: str | None) -> None:
    if key != get_settings().admin_token:
        raise HTTPException(status_code=403)


def _check_user(user_id: str) -> None:
    if not _WEB_ID.match(user_id):
        raise HTTPException(status_code=400, detail="invalid user id")


class SendIn(BaseModel):
    user_id: str
    name: str = Field(default="Guest", max_length=40)
    text: str = Field(default="", max_length=2000)
    button_id: str | None = Field(default=None, max_length=100)


@router.get("", response_class=HTMLResponse)
async def page(key: str = ""):
    _check(key)
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))


@router.post("/api/send")
async def send(body: SendIn, background: BackgroundTasks, x_demo_key: str | None = Header(default=None)):
    _check(x_demo_key)
    _check_user(body.user_id)
    if not body.text.strip() and not body.button_id:
        raise HTTPException(status_code=400, detail="empty message")
    msg = InboundMessage(
        wa_id=body.user_id,
        wa_message_id=f"web-{uuid.uuid4()}",
        type="button_reply" if body.button_id else "text",
        text=body.text.strip(),
        profile_name=body.name.strip() or "Guest",
        button_id=body.button_id,
    )
    uid = await ingest(msg)
    if uid:
        background.add_task(process_user, uid)
    return {"ok": True}


@router.get("/api/messages")
async def messages(user_id: str = Query(...), x_demo_key: str | None = Header(default=None)):
    _check(x_demo_key)
    _check_user(user_id)
    async with session_scope() as db:
        user = await db.scalar(select(User).where(User.wa_id == user_id))
        if not user:
            return {"messages": [], "pending": False}
        rows = list(
            await db.scalars(
                select(Message).where(Message.user_id == user.id).order_by(Message.created_at.desc()).limit(150)
            )
        )
        pending = any(m.direction == "in" and m.status == "received" for m in rows)
        out = []
        for m in reversed(rows):
            if m.type == "system":
                continue
            out.append(
                {
                    "id": str(m.id),
                    "dir": m.direction,
                    "type": m.type,
                    "body": m.body,
                    "buttons": (m.payload or {}).get("buttons", []) if m.type == "buttons" else [],
                    "at": m.created_at.isoformat(),
                }
            )
        return {"messages": out, "pending": pending, "mode": user.conversation_mode}


@router.get("/api/staff")
async def staff(x_demo_key: str | None = Header(default=None)):
    _check(x_demo_key)
    async with session_scope() as db:
        rows = list(await db.scalars(select(Handoff).order_by(Handoff.created_at.desc()).limit(30)))
        return {
            "handoffs": [
                {"id": str(h.id), "urgency": h.urgency, "reason": h.reason, "status": h.status,
                 "summary": h.summary, "at": h.created_at.isoformat()}
                for h in rows
            ]
        }
