"""DEMO ONLY: mock availability and booking.

Slots are generated deterministically so a demo is repeatable. The real build replaces
MockBookingAdapter with a Vagaro adapter behind the same two methods.
"""

import hashlib
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, User

SLOT_TIMES = [time(10, 0), time(11, 0), time(14, 0), time(14, 30), time(15, 30), time(16, 30)]
WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class MockBookingAdapter:
    def __init__(self, tz: str) -> None:
        self.tz = ZoneInfo(tz)

    def _is_free(self, dt: datetime) -> bool:
        h = int(hashlib.md5(dt.isoformat().encode()).hexdigest(), 16)
        return h % 3 != 0  # ~2/3 of slots free

    def availability(self, preferred_day: str | None, earliest: date | None = None, limit: int = 3) -> list[datetime]:
        now = datetime.now(self.tz)
        start = max(earliest or now.date(), now.date() + timedelta(days=1))
        target_wd = None
        if preferred_day:
            p = preferred_day.lower()
            target_wd = next((i for i, n in enumerate(WEEKDAY_NAMES) if n in p), None)
            if "next" in p and target_wd is not None:
                # "next Thursday" = the Thursday of next week
                start = max(start, now.date() + timedelta(days=7 - now.weekday()))
        if target_wd is not None and target_wd >= 5:
            target_wd = None  # clinic is closed on weekends – fall back to the nearest weekdays
        slots: list[datetime] = []
        for offset in range(0, 21):
            d = start + timedelta(days=offset)
            if d.weekday() >= 5 or (target_wd is not None and d.weekday() != target_wd):
                continue
            for t in SLOT_TIMES:
                dt = datetime.combine(d, t, tzinfo=self.tz)
                if self._is_free(dt):
                    slots.append(dt)
            if len(slots) >= limit:
                break
        return slots[:limit]

    async def book(
        self, db: AsyncSession, user: User, starts_at: datetime, purpose: str, provider_name: str,
        journey_id: uuid.UUID | None, notes: str | None,
    ) -> Appointment:
        appt = Appointment(
            user_id=user.id,
            journey_id=journey_id,
            starts_at=starts_at,
            purpose=purpose,
            provider_name=provider_name,
            notes=notes,
        )
        db.add(appt)
        await db.flush()
        return appt


async def upcoming_appointments(db: AsyncSession, user_id: uuid.UUID) -> list[Appointment]:
    return list(
        await db.scalars(
            select(Appointment)
            .where(Appointment.user_id == user_id, Appointment.status == "confirmed")
            .order_by(Appointment.starts_at)
        )
    )


def fmt_slot(dt: datetime) -> str:
    return dt.strftime("%A, %b ") + str(dt.day) + dt.strftime(" at %-I:%M %p")
