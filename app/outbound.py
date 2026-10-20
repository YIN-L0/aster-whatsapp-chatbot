"""Send + persist outbound messages, respecting the WhatsApp 24h customer-service window."""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import Button, get_channel, is_web_user
from app.config import get_settings
from app.models import Message, User

log = logging.getLogger(__name__)


def in_service_window(user: User) -> bool:
    if is_web_user(user.wa_id):
        return True  # the 24h rule is a WhatsApp restriction; web chat can always receive messages
    if user.last_inbound_at is None:
        return False
    last = user.last_inbound_at
    if last.tzinfo is None:  # sqlite returns naive datetimes
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last < timedelta(hours=23, minutes=50)


async def send_text(
    db: AsyncSession,
    user: User,
    text: str,
    *,
    sender: str = "aster_ai",
    agent_run_id: uuid.UUID | None = None,
    checkin_id: uuid.UUID | None = None,
) -> bool:
    if not in_service_window(user):
        log.warning("outside 24h window, free text blocked user=%s", user.id)
        return False
    result = await get_channel().send_text(user.wa_id, text)
    db.add(
        Message(
            user_id=user.id,
            direction="out",
            wa_message_id=result.wa_message_id,
            type="text",
            body=text,
            sender=sender,
            status="processed",
            delivery_status="sent" if result.ok else "failed",
            agent_run_id=agent_run_id,
            checkin_id=checkin_id,
        )
    )
    return result.ok


async def send_buttons(db: AsyncSession, user: User, body: str, buttons: list[Button], payload: dict | None = None) -> bool:
    if not in_service_window(user):
        return False
    result = await get_channel().send_buttons(user.wa_id, body, buttons)
    db.add(
        Message(
            user_id=user.id,
            direction="out",
            wa_message_id=result.wa_message_id,
            type="buttons",
            body=body,
            payload={"buttons": [b.__dict__ for b in buttons], **(payload or {})},
            sender="aster_system",
            status="processed",
            delivery_status="sent" if result.ok else "failed",
        )
    )
    return result.ok


async def send_checkin_template(db: AsyncSession, user: User, checkin_id: uuid.UUID) -> bool:
    """Used outside the 24h window. Template text must not contain treatment or symptoms."""
    name = get_settings().wa_checkin_template
    if not name:
        log.warning("no WA_CHECKIN_TEMPLATE configured; cannot reach user=%s outside window", user.id)
        return False
    params = [user.first_name or "there"]
    result = await get_channel().send_template(user.wa_id, name, params)
    db.add(
        Message(
            user_id=user.id,
            direction="out",
            wa_message_id=result.wa_message_id,
            type="template",
            body=f"<template {name}>",
            payload={"params": params},
            sender="aster_system",
            status="processed",
            delivery_status="sent" if result.ok else "failed",
            checkin_id=checkin_id,
        )
    )
    return result.ok


async def notify_staff(text: str) -> None:
    """Demo notifier: WhatsApp message to staff numbers (they must have messaged the bot in the last 24h)."""
    numbers = get_settings().staff_numbers
    channel = get_channel()
    for number in numbers:
        res = await channel.send_text(number, text)
        if not res.ok:
            log.error("staff notification failed to=%s err=%s", number[-4:], res.error)
    log.info("staff notified count=%s", len(numbers))
