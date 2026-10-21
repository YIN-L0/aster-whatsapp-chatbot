"""Follow-up Engine: protocol → scheduled check-ins → proactive WhatsApp messages."""

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import journeys
from app.config import get_settings
from app.models import Appointment, Checkin, Journey, User, utcnow

log = logging.getLogger(__name__)


def _send_hour() -> time:
    start = get_settings().checkin_send_window.split("-")[0]
    h, m = start.split(":")
    return time(int(h) + 1, int(m))  # an hour after the window opens, e.g. 10:00


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def plan_for_journey(db: AsyncSession, user: User, journey: Journey) -> list[Checkin]:
    """(Re)generate future recovery check-ins. Idempotent: cancels unsent ones first."""
    await db.execute(
        update(Checkin)
        .where(Checkin.journey_id == journey.id, Checkin.status == "scheduled", Checkin.kind == "recovery")
        .values(status="cancelled")
    )
    proto = journeys.get_protocol(journey.treatment_slug)
    tz = ZoneInfo(user.timezone)
    today_day = journeys.day_post_treatment(user, journey)
    created = []
    for i, step in enumerate(proto["schedule"]):
        if step["day"] <= today_day:
            continue
        due_local = datetime.combine(journey.treatment_date + timedelta(days=step["day"]), _send_hour(), tzinfo=tz)
        c = Checkin(
            user_id=user.id, journey_id=journey.id, kind="recovery",
            protocol_step=i, day_offset=step["day"], due_at=due_local.astimezone(timezone.utc),
        )
        db.add(c)
        created.append(c)
    await db.flush()
    return created


async def schedule_appointment_reminder(db: AsyncSession, user: User, appt: Appointment) -> None:
    tz = ZoneInfo(user.timezone)
    start = _aware(appt.starts_at).astimezone(tz)
    due = datetime.combine(start.date() - timedelta(days=1), _send_hour(), tzinfo=tz)
    if due <= datetime.now(tz):
        due = max(datetime.now(tz) + timedelta(minutes=1), start - timedelta(hours=2))
    db.add(Checkin(user_id=user.id, appointment_id=appt.id, kind="appointment_reminder", due_at=due.astimezone(timezone.utc)))
    await db.flush()


async def send_checkin(db: AsyncSession, user: User, checkin: Checkin) -> bool:
    from app import booking, outbound
    from app.pipeline import run_checkin_opener

    if not user.proactive_opt_in or user.conversation_mode == "paused":
        checkin.status = "cancelled"
        return False
    if checkin.kind == "appointment_reminder":
        appt = await db.get(Appointment, checkin.appointment_id)
        if not appt or appt.status != "confirmed":
            checkin.status = "cancelled"
            return False
        when = booking.fmt_slot(_aware(appt.starts_at).astimezone(ZoneInfo(user.timezone)))
        text = (f"Hi {user.first_name or 'there'}! 💛 A quick reminder: your {appt.purpose.replace('_', ' ')} "
                f"at Essence is on {when}. Reply here if you need to change it.")
        ok = await outbound.send_text(db, user, text, sender="aster_system", checkin_id=checkin.id)
    elif outbound.in_service_window(user):
        ok = await run_checkin_opener(db, user, checkin)
    else:
        ok = await outbound.send_checkin_template(db, user, checkin.id)
    checkin.status = "sent" if ok else "failed"
    checkin.sent_at = utcnow()
    return ok


async def active_checkin(db: AsyncSession, user: User) -> Checkin | None:
    """The most recent sent check-in within 48h that the patient has not answered yet."""
    since = utcnow() - timedelta(hours=48)
    return await db.scalar(
        select(Checkin)
        .where(Checkin.user_id == user.id, Checkin.status == "sent", Checkin.kind == "recovery", Checkin.sent_at >= since)
        .order_by(Checkin.sent_at.desc())
    )


async def run_due(db: AsyncSession) -> int:
    rows = list(await db.scalars(
        select(Checkin).where(Checkin.status == "scheduled", Checkin.due_at <= utcnow()).order_by(Checkin.due_at).limit(20)
    ))
    sent = 0
    for c in rows:
        user = await db.get(User, c.user_id)
        if user and await send_checkin(db, user, c):
            sent += 1
    await db.execute(
        update(Checkin)
        .where(Checkin.status == "sent", Checkin.kind == "recovery", Checkin.sent_at < utcnow() - timedelta(hours=48))
        .values(status="missed")
    )
    return sent


async def scheduler_loop(stop: asyncio.Event) -> None:
    from app.db import session_scope

    interval = get_settings().scheduler_interval_seconds
    while not stop.is_set():
        try:
            async with session_scope() as db:
                n = await run_due(db)
                if n:
                    log.info("scheduler sent %s check-ins", n)
        except Exception:
            log.exception("scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
