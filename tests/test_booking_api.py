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
    assert out["error"] == "need_patient_details" and out["missing"] == ["full name"]

    set_provider(FakeProvider([
        [{"name": "book_appointment", "input": {**book, "patient_full_name": "Emily Stone"}}],
        reply("You're all set! 💛 Botox with Claire tomorrow at 3:30 PM.", intents=["booking"]),
    ]))
    await handle_inbound(inbound("Emily Stone, April 2 1990"))
    assert "all set" in channel.sent[-1]["text"]

    post = [r for r in backend.requests if r.method == "POST"][-1]
    body = json.loads(post.content)
    # Demo: no birth date asked; the configured placeholder is sent
    assert body["patient"] == {"name": "Emily Stone", "phone": "+15551230000", "birthDate": "1990-01-01", "email": None}
    assert body["doctor"] == "claire-benson" and body["service"] == "botox" and post.headers["idempotency-key"]
    async with session_scope() as db:
        appt = await db.scalar(select(Appointment))
        assert (appt.external_ref, appt.status, appt.source) == ("apt_test1", "confirmed", "aster_api")
        assert (await db.scalar(select(User))).full_name == "Emily Stone"
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


async def test_patient_words_match_backend_codes(monkeypatch):
    names = {'inmode-ignite': 'InMode Ignite RF', 'trusculpt': 'truSculpt iD', 'consultation': 'Consultation (new patient / treatment planning)', 'wellness-consult': 'Wellness / functional medicine consultation', 'keravive': 'HydraFacial Keravive scalp', 'prp-hair': 'PRP hair injections / Nutrafol', 'botox': 'Botox', 'dermal-fillers': 'Dermal fillers (Restylane, Belotero, Radiesse, Juvederm)', 'dysport': 'Dysport', 'fat-dissolving': 'Fat-dissolving injection (under the chin)', 'filler-reversal': 'Filler reversal (Hylenex)', 'prp-facial': 'PRP facial / PRP Selphyller', 'sculptra': 'Sculptra / hyperdiluted Radiesse', 'iv-drip': "IV drips (Myers', Immunity, Quench and others)", 'vitamin-injection': 'Vitamin injections (B12, Vitamin D and others)', 'peptides': 'Peptides (NAD+, BPC-157, Sermorelin and others)', 'chemical-peel': 'Chemical peels (Prodigy, PRXt-33, BioRepeel)', 'pico-laser': 'Discovery Pico laser', 'hydrafacial': 'HydraFacial (Signature, Platinum)', 'ipl-ndyag': 'IPL / ND YAG laser', 'facial': 'Medical-grade facial / Express Harmony Facial / dermaplaning', 'microneedling': 'Microneedling / RF microneedling (+ PRP $250)', 'co2-laser': 'Tetra CO2 laser', 'lab-testing': 'Testing (hormone panels, GI mapping, food sensitivity, mold, heavy metals)', 'weight-loss': 'Semaglutide / tirzepatide program', 'weight-loss-consult': 'Weight-loss consultation', 'weight-loss-followup': 'Weight-loss follow-up', 'hormone-health': 'BHRT / intimate wellness / TRT'}  # live catalog, 2026-10-23
    services = [{"code": c, "name": n, "botBookable": True} for c, n in names.items()]
    doctors = [{"code": f"{f}-{l}".lower(), "name": f"{f} {l}, RN", "services": []} for f, l in
               [("Diane", "Mercer"), ("Megan", "Clarke"), ("Melissa", "Rowe"), ("Claire", "Benson"),
                ("Laura", "Whitfield"), ("Daniel", "Hayes")]]
    api = booking.ApiBookingAdapter("https://catalog.test", "k", "America/New_York")
    booking._catalog_cache[api.base] = (float("inf"), {"doctors": doctors, "services": services})
    monkeypatch.setattr(booking.ApiBookingAdapter, "CATALOG_TTL", float("inf"))
    expected = {"Botox": "botox", "dermal fillers": "dermal-fillers", "HydraFacial": "hydrafacial",
                "chemical peel": "chemical-peel", "CO2 laser": "co2-laser", "IV drip": "iv-drip",
                "weight loss consultation": "weight-loss-consult", "Keravive scalp treatment": "keravive",
                "PRP hair": "prp-hair", "truSculpt": "trusculpt"}
    for text, code in expected.items():
        assert (await api.find_service(text))["code"] == code, text
    assert await api.find_service("pizza") is None
    assert await api.find_service("laser") is None  # three lasers: ask, don't guess
    for text, code in {"Claire": "claire-benson", "claire benson": "claire-benson", "Dr. Mercer": "diane-mercer",
                       "Daniel": "daniel-hayes", "megan-clarke": "megan-clarke"}.items():
        assert (await api.find_doctor(text))["code"] == code, text
    booking._catalog_cache.clear()


async def test_offer_buttons_then_tap_to_book(channel, backend):
    await onboard(channel)
    async with session_scope() as db:
        user = await db.scalar(select(User))
        user.full_name, user.birth_date = "Emily Stone", "1990-04-02"
    set_provider(FakeProvider([
        [{"name": "check_availability", "input": {"purpose": "new_treatment", "service": "Botox"}}],
        reply("Botox starts at $13/unit. Claire's next opening is tomorrow at 3:30 PM – want me to book it?",
              intents=["service_question", "booking"]),
    ]))
    await handle_inbound(inbound("How much is Botox?"))
    offer = channel.sent[-1]
    assert offer["type"] == "buttons" and offer["buttons"][-1]["id"] == "slot:other"
    first = offer["buttons"][0]
    assert first["id"].endswith("|claire-benson|botox") and first["title"].endswith(" Claire") and len(first["title"]) <= 20

    slot = first["id"].removeprefix("slot:").split("|")[0]
    fake = FakeProvider([
        [{"name": "book_appointment", "input": {"slot": slot, "purpose": "new_treatment", "service": "botox",
                                                "provider": "claire-benson", "notes_for_provider": "-"}}],
        reply("You're all set! 💛", intents=["booking"]),
    ])
    set_provider(fake)
    await handle_inbound(inbound(first["title"], button_id=first["id"]))
    patient_said = [m["content"] for m in fake.calls[0].messages if m["role"] == "user"][-1]
    assert f"[slot={slot} provider=claire-benson service=botox]" in patient_said
    assert "all set" in channel.sent[-1]["text"]  # no stale time buttons after booking
    async with session_scope() as db:
        assert (await db.scalar(select(Appointment))).external_ref == "apt_test1"


async def test_availability_queries_doctors_and_days_in_parallel(backend):
    """Slot lookups for several days and doctors go out together, and the earliest slots still come first."""
    import asyncio

    in_flight = peak = 0
    orig = booking.ApiBookingAdapter._request

    async def slow_request(self, method, path, headers=None, **kw):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        try:
            return await orig(self, method, path, headers, **kw)
        finally:
            in_flight -= 1

    booking.ApiBookingAdapter._request = slow_request
    try:
        api = booking.api_adapter()
        await api.catalog()
        slots = await api.availability("consultation", None, None)
    finally:
        booking.ApiBookingAdapter._request = orig
    assert peak > 1
    assert [s["start"] for s in slots] == sorted(s["start"] for s in slots)
    today = datetime.now(api.tz).date().isoformat()
    assert slots[0]["start"].startswith(today) and len(slots) == 3
    slot_days = {r.url.params["date"] for r in backend.requests if r.url.path.endswith("/slots")}
    assert len(slot_days) == booking.ApiBookingAdapter.DAYS_PER_BATCH  # stopped after the first batch


async def test_rate_limited_request_is_retried(backend, monkeypatch):
    monkeypatch.setattr(booking.ApiBookingAdapter, "RETRY_WAITS", (0, 0, 0))
    orig, calls = backend.handler, []

    def flaky(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(429, json={"code": "RATE_LIMITED", "message": "Too many requests."})
        return orig(req)

    monkeypatch.setattr(booking.ApiBookingAdapter, "transport", httpx.MockTransport(flaky))
    assert (await booking.api_adapter()._request("GET", "/services"))[0]["code"] == "botox"
    assert len(calls) == 2


def test_slot_buttons_prefer_different_times_and_name_the_provider():
    from app.agent import slot_buttons
    diane = {"doctor": "diane-mercer", "doctor_name": "Diane Mercer, FNP-BC"}
    melissa = {"doctor": "melissa-rowe", "doctor_name": "Melissa Rowe, PA-C"}
    slots = [{"start": "2026-10-30T09:00:00", **diane}, {"start": "2026-10-30T09:00:00", **melissa},
             {"start": "2026-12-31T12:30:00", **melissa}]
    titles = [b.title for b in slot_buttons(slots, "botox")]
    assert titles == ["10/30 9AM Diane", "12/31 12:30PM", "Other times"]  # second time differs; name too long to fit

    same_time = [slots[0], slots[1]]
    titles = [b.title for b in slot_buttons(same_time, "botox")]
    assert titles[:2] == ["10/30 9AM Diane", "10/30 9AM Melissa"]  # only option: same time, told apart by name
    assert all(len(t) <= 20 for t in titles)
