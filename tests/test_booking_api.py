"""Booking against the Aster booking backend (Bot API v1), with a fake backend behind httpx.MockTransport."""

import json
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app import booking
from app.config import get_settings
from app.db import session_scope
from app.llm import FakeProvider, set_provider
from app.models import AgentRun, Appointment, Checkin, User
from app.pipeline import handle_inbound
from tests.conftest import inbound
from tests.test_flows import onboard, reply

DOCTORS = [
    {"id": 11, "code": "claire-benson", "name": "Claire Benson, RN", "services": ["consultation", "botox"]},
    {"id": 12, "code": "megan-clarke", "name": "Megan Clarke, FNP-BC", "services": ["weight-loss-consult"]},
]
SERVICES = [
    {"id": 5, "code": "botox", "name": "Botox", "durationMinutes": 30, "requiresConsult": False, "botBookable": True},
    {"id": 6, "code": "consultation", "name": "Consultation", "durationMinutes": 30, "requiresConsult": False,
     "botBookable": True},
    {"id": 7, "code": "weight-loss", "name": "Weight Loss Program", "durationMinutes": 45, "requiresConsult": True,
     "botBookable": False},
    {"id": 8, "code": "weight-loss-consult", "name": "Weight Loss Consultation", "durationMinutes": 30,
     "requiresConsult": False, "botBookable": True},
]


class FakeBackend:
    def __init__(self, status: str = "SCHEDULED") -> None:
        self.status = status
        self.taken: set[str] = set()
        self.requests: list[httpx.Request] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if req.headers.get("authorization") != "Bearer ak_test":
            return httpx.Response(401, json={"code": "UNAUTHORIZED", "message": "bad key"})
        path = req.url.path.removeprefix("/api/bot/v1")
        if path == "/doctors":
            return httpx.Response(200, json=DOCTORS)
        if path == "/services":
            return httpx.Response(200, json=SERVICES)
        if path.endswith("/slots"):
            day = req.url.params["date"]
            return httpx.Response(200, json={"slots": [f"{day}T15:30:00-04:00", f"{day}T16:00:00-04:00"]})
        if path == "/appointments":
            body = json.loads(req.content)
            if body["start"] in self.taken:
                return httpx.Response(409, json={"code": "SLOT_TAKEN", "message": "taken"})
            self.taken.add(body["start"])
            doctor = next(d for d in DOCTORS if d["code"] == body["doctor"])
            return httpx.Response(201, json={
                "ref": "apt_test1", "status": self.status, "start": body["start"],
                "doctor": {"code": doctor["code"], "name": doctor["name"]},
                "service": {"code": body["service"], "name": "Botox"},
                "store": {"name": "Essence Aesthetics & Wellness", "address": "Demo location"},
            })
        return httpx.Response(404, json={"code": "NOT_FOUND", "message": path})


@pytest.fixture
def backend(monkeypatch):
    fake = FakeBackend()
    s = get_settings()
    monkeypatch.setattr(s, "booking_api_url", "https://booking.test/api/bot/v1")
    monkeypatch.setattr(s, "booking_api_key", "ak_test")
    monkeypatch.setattr(booking.ApiBookingAdapter, "transport", httpx.MockTransport(fake.handler))
    booking._catalog_cache.clear()
    return fake


async def _last_tool_outputs() -> list[dict]:
    async with session_scope() as db:
        run = (await db.scalars(select(AgentRun).where(AgentRun.kind == "main").order_by(AgentRun.created_at.desc()))).first()
        return [c["output"] for c in run.tool_calls]


def _tomorrow_slot() -> str:
    return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT15:30:00-04:00")


async def test_check_availability_and_book_after_asking_details(channel, backend):
    await onboard(channel)
    set_provider(FakeProvider([
        [{"name": "check_availability", "input": {"purpose": "new_treatment", "service": "Botox", "provider": "Claire"}}],
        reply("Claire has an opening tomorrow at 3:30 PM. Would that work?", intents=["booking"]),
    ]))
    await handle_inbound(inbound("Can I book Botox with Claire?"))
    out = (await _last_tool_outputs())[0]
    assert out["service"] == "Botox" and out["slots"][0]["provider"] == "Claire Benson, RN"
    slot = out["slots"][0]["slot"]

    book = {"slot": slot, "purpose": "new_treatment", "service": "Botox", "provider": "Claire Benson, RN",
            "notes_for_provider": "First time Botox"}
    set_provider(FakeProvider([
        [{"name": "book_appointment", "input": book}],
        reply("Before I book, what's your full name and date of birth?", intents=["booking"]),
    ]))
    await handle_inbound(inbound("Yes"))
    out = (await _last_tool_outputs())[0]
    assert out["error"] == "need_patient_details" and out["missing"] == ["full name", "date of birth"]

    set_provider(FakeProvider([
        [{"name": "book_appointment", "input": {**book, "patient_full_name": "Emily Stone",
                                                "patient_birth_date": "1990-04-02"}}],
        reply("You're all set! 💛 Botox with Claire tomorrow at 3:30 PM.", intents=["booking"]),
    ]))
    await handle_inbound(inbound("Emily Stone, April 2 1990"))
    assert "all set" in channel.sent[-1]["text"]

    post = [r for r in backend.requests if r.method == "POST"][-1]
    body = json.loads(post.content)
    assert body["patient"] == {"name": "Emily Stone", "phone": "+15551230000", "birthDate": "1990-04-02", "email": None}
    assert body["doctor"] == "claire-benson" and body["service"] == "botox" and post.headers["idempotency-key"]
    async with session_scope() as db:
        appt = await db.scalar(select(Appointment))
        assert (appt.external_ref, appt.status, appt.source) == ("apt_test1", "confirmed", "aster_api")
        assert (await db.scalar(select(User))).birth_date == "1990-04-02"
        assert await db.scalar(select(Checkin).where(Checkin.kind == "appointment_reminder"))


async def test_consult_only_service_and_slot_taken(channel, backend):
    await onboard(channel)
    backend.taken.add(_tomorrow_slot())
    async with session_scope() as db:
        user = await db.scalar(select(User))
        user.full_name, user.birth_date = "Emily Stone", "1990-04-02"
    set_provider(FakeProvider([
        [{"name": "check_availability", "input": {"purpose": "new_treatment", "service": "weight loss program"}}],
        [{"name": "book_appointment", "input": {"slot": _tomorrow_slot(), "purpose": "new_treatment",
                                                "service": "Botox", "provider": "Claire", "notes_for_provider": "-"}}],
        reply("That time was just taken – how about 4:00 PM?", intents=["booking"]),
    ]))
    await handle_inbound(inbound("I want semaglutide, and book me Botox tomorrow 3:30"))
    consult, taken = await _last_tool_outputs()
    assert consult["error"] == "needs_consultation_first" and "Weight Loss Consultation" in consult["book_instead"]
    assert taken["error"] == "slot_taken"
    async with session_scope() as db:
        assert await db.scalar(select(Appointment)) is None


async def test_pending_booking_is_not_called_confirmed(channel, backend):
    backend.status = "PENDING"
    await onboard(channel)
    async with session_scope() as db:
        user = await db.scalar(select(User))
        user.full_name, user.birth_date = "Emily Stone", "1990-04-02"
    set_provider(FakeProvider([
        [{"name": "book_appointment", "input": {"slot": _tomorrow_slot(), "purpose": "consultation",
                                                "service": "consultation", "provider": "Claire", "notes_for_provider": "-"}}],
        reply("Your request is in – the clinic will confirm it shortly.", intents=["booking"]),
    ]))
    await handle_inbound(inbound("Book that consultation please"))
    assert (await _last_tool_outputs())[0]["status"] == "pending_clinic_confirmation"
    async with session_scope() as db:
        assert (await db.scalar(select(Appointment))).status == "pending"
        assert await db.scalar(select(Checkin).where(Checkin.kind == "appointment_reminder")) is None
