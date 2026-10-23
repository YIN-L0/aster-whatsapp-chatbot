"""Availability and booking.

ApiBookingAdapter books into the Aster booking backend (Bot API v1) when BOOKING_API_URL is set.
Otherwise MockBookingAdapter generates deterministic demo slots so a demo is repeatable.
"""

import asyncio
import hashlib
import logging
import re
import time as clock
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Appointment, User

log = logging.getLogger(__name__)

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
        target_wd, start = _parse_preferred_day(preferred_day, now.date(), start)
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


def _parse_preferred_day(preferred_day: str | None, today: date, start: date) -> tuple[int | None, date]:
    """'Thursday' → (3, start); 'next Thursday' → the Thursday of next week."""
    if not preferred_day:
        return None, start
    p = preferred_day.lower()
    target_wd = next((i for i, n in enumerate(WEEKDAY_NAMES) if n in p), None)
    if "next" in p and target_wd is not None:
        start = max(start, today + timedelta(days=7 - today.weekday()))
    return target_wd, start


_STOPWORDS = {"the", "and", "for", "with", "treatment", "appointment", "session", "therapy"}


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.split(r"[^a-z0-9+]+", text.lower()) if len(w) > 2 and w not in _STOPWORDS}


def _best_match(text: str, items: list[dict]) -> dict | None:
    """Exact code or name first, then the one item sharing the most words ('Dr. Mercer' → Diane Mercer).
    A tie ('laser' fits three lasers) returns None, so the model asks instead of guessing."""
    t = text.strip().lower()
    for it in items:
        if t in (it["code"], it["code"].replace("-", " "), it["name"].lower()):
            return it
    words = _words(t)
    score = lambda it: len(words & _words(f"{it['name']} {it['code']}"))  # noqa: E731
    ranked = sorted(items, key=score, reverse=True)
    if not ranked or not score(ranked[0]) or (len(ranked) > 1 and score(ranked[1]) == score(ranked[0])):
        return None
    return ranked[0]


class BookingError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


_catalog_cache: dict[str, tuple[float, dict]] = {}


class ApiBookingAdapter:
    """Aster Bot API v1. Doctors and services are addressed by their stable codes ('claire-benson', 'botox')."""

    CATALOG_TTL = 600
    RETRY_WAITS = (0, 2, 4, 8)  # the backend answers 502/503 for up to a minute while it redeploys
    transport: httpx.AsyncBaseTransport | None = None  # tests swap in httpx.MockTransport

    def __init__(self, base_url: str, api_key: str, tz: str) -> None:
        self.base = base_url.rstrip("/")
        self.tz = ZoneInfo(tz)
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def _request(self, method: str, path: str, headers: dict | None = None, **kw) -> dict | list:
        async with httpx.AsyncClient(timeout=15, transport=self.transport) as client:
            for wait in self.RETRY_WAITS:
                await asyncio.sleep(wait)
                try:
                    r = await client.request(method, self.base + path, headers={**self._headers, **(headers or {})}, **kw)
                except httpx.HTTPError as exc:
                    log.warning("booking api %s %s failed: %s", method, path, exc)
                    continue
                if r.status_code not in (502, 503, 504):
                    break
            else:
                raise BookingError("UNAVAILABLE", "Booking system did not answer")
        if r.status_code >= 400:
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            log.warning("booking api %s %s → %s %s", method, path, r.status_code, body.get("code"))
            raise BookingError(body.get("code", f"HTTP_{r.status_code}"), body.get("message", ""))
        return r.json()

    async def catalog(self) -> dict:
        cached = _catalog_cache.get(self.base)
        if cached and clock.monotonic() - cached[0] < self.CATALOG_TTL:
            return cached[1]
        # Only doctors with a stable code can be booked by the bot; others belong to stores this bot doesn't serve
        doctors = [d for d in await self._request("GET", "/doctors") if d.get("code")]
        cat = {"doctors": doctors, "services": await self._request("GET", "/services")}
        _catalog_cache[self.base] = (clock.monotonic(), cat)
        return cat

    async def find_service(self, text: str) -> dict | None:
        return _best_match(text, (await self.catalog())["services"])

    async def find_doctor(self, text: str) -> dict | None:
        return _best_match(text, (await self.catalog())["doctors"])

    async def availability(self, service: str, doctor: str | None, preferred_day: str | None, limit: int = 3) -> list[dict]:
        """Up to `limit` real slots, earliest first: [{'start': iso, 'doctor': code, 'doctor_name': ...}]."""
        doctors = [d for d in (await self.catalog())["doctors"] if service in d["services"]]
        if doctor:
            doctors = [d for d in doctors if d["code"] == doctor]
        today = datetime.now(self.tz).date()
        target_wd, start = _parse_preferred_day(preferred_day, today, today)
        days = [start + timedelta(days=i) for i in range(21 if target_wd is not None else 7)]
        if target_wd is not None:
            days = [d for d in days if d.weekday() == target_wd]
        found: list[dict] = []
        for day in days:
            for d in doctors:
                res = await self._request("GET", f"/doctors/{d['code']}/slots",
                                          params={"date": day.isoformat(), "service": service})
                found += [{"start": s, "doctor": d["code"], "doctor_name": d["name"]} for s in res["slots"]]
            if len(found) >= limit:
                break
        return sorted(found, key=lambda s: s["start"])[:limit]

    async def book(self, *, doctor: str, service: str, start: str, patient: dict, note: str, idempotency_key: str) -> dict:
        """Raises BookingError('SLOT_TAKEN') when someone else got the time first."""
        return await self._request(
            "POST", "/appointments", headers={"Idempotency-Key": idempotency_key},
            json={"doctor": doctor, "service": service, "start": start, "patient": patient, "note": note},
        )

    async def appointments_for(self, phone: str) -> list[dict]:
        return await self._request("POST", "/appointments/search", json={"phone": phone})


def api_adapter() -> ApiBookingAdapter | None:
    s = get_settings()
    if not s.booking_api_url:
        return None
    return ApiBookingAdapter(s.booking_api_url, s.booking_api_key, s.clinic_timezone)


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
