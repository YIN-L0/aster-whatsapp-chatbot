"""Location (ZIP / city only), nearby search links and one-tap calendar links (docs: need 3, Calendar and Location)."""

import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from app import places
from app.channels import ConsoleChannel, InboundMessage, RoutingChannel, parse_webhook, set_channel
from app.config import get_settings
from app.db import session_scope
from app.llm import FakeProvider, set_provider
from app.main import app
from app.models import Appointment, Checkin, Message, User
from app.pipeline import handle_inbound
from tests.conftest import inbound

PIN = (39.97839, -86.11804)  # Carmel, IN


def reply(*messages):
    return {"messages": list(messages), "risk_level": 0, "risk_reason": "", "intents": ["smalltalk"]}


def _geocoder(calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        return httpx.Response(200, json={"address": {"city": "Carmel", "state": "Indiana", "ISO3166-2-lvl4": "US-IN",
                                                     "postcode": "46032", "country_code": "us"}})
    return httpx.MockTransport(handler)


async def _user() -> User:
    async with session_scope() as db:
        return await db.scalar(select(User))


async def consent(channel):
    set_provider(FakeProvider([]))
    await handle_inbound(inbound("hi"))
    await handle_inbound(inbound("I Agree", button_id="consent:yes"))


def located(lat: float, lon: float) -> InboundMessage:
    m = inbound("")
    m.type, m.latitude, m.longitude = "location", lat, lon
    return m


async def test_whatsapp_location_message_is_parsed():
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "15551230000", "id": "wamid.loc1", "type": "location",
         "location": {"latitude": PIN[0], "longitude": PIN[1]}}]}}]}]}
    msgs, _ = parse_webhook(payload)
    assert (msgs[0].type, msgs[0].latitude, msgs[0].longitude) == ("location", *PIN)


async def test_search_without_location_asks_for_it_then_a_shared_pin_keeps_only_the_area(channel, monkeypatch):
    calls = []
    monkeypatch.setattr(places, "_transport", _geocoder(calls))
    await consent(channel)

    set_provider(FakeProvider([[{"name": "search_nearby", "input": {"query": "registered dietitian"}}],
                               reply("Happy to look near you!")]))
    await handle_inbound(inbound("Can you find a dietitian near me?"))
    assert channel.sent[-1] == {"to": "15551230000", "type": "location_request", "text": channel.sent[-1]["text"]}
    assert "ZIP code" in channel.sent[-1]["text"]

    fake = FakeProvider([[{"name": "search_nearby", "input": {"query": "registered dietitian"}}],
                         reply("Here's a search near you.")])
    set_provider(fake)
    await handle_inbound(located(*PIN))
    assert calls and calls[0]["lat"] == "39.97839"
    user = await _user()
    assert (user.location_zip, user.location_city, user.location_state) == ("46032", "Carmel", "IN")
    nearby = channel.sent[-1]["text"]
    assert "near Carmel, IN 46032" in nearby and "not Aster partners" in nearby
    assert "google.com/maps/search/?api=1&query=registered+dietitian+near+Carmel%2C+IN+46032" in nearby

    # the coordinates are never stored: not in messages, not in what the agent saw
    async with session_scope() as db:
        bodies = [m.body or "" for m in await db.scalars(select(Message))]
        payloads = [json.dumps(m.payload or {}) for m in await db.scalars(select(Message))]
    stored = "\n".join(bodies + payloads)
    assert "39.97" not in stored and "86.11" not in stored
    assert "📍 Shared my location: Carmel, IN 46032" in bodies
    assert "39.97" not in "\n".join(str(m.get("content", "")) for m in fake.calls[0].messages)


async def test_failed_geocoding_and_location_before_consent_store_nothing(channel, monkeypatch):
    def broken(request):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(places, "_transport", httpx.MockTransport(broken))
    set_provider(FakeProvider([]))
    await handle_inbound(located(*PIN))  # before consent: not even looked up
    async with session_scope() as db:
        assert (await db.scalar(select(Message).where(Message.type == "location"))).body == "📍 Shared a location"
    await handle_inbound(inbound("I Agree", button_id="consent:yes"))
    set_provider(FakeProvider([reply("Could you tell me your ZIP code?")]))
    await handle_inbound(located(*PIN))
    user = await _user()
    assert user.location_zip is None and user.location_city is None


async def test_typed_zip_is_validated_and_saved(channel):
    await consent(channel)
    set_provider(FakeProvider([[{"name": "set_location", "input": {"zip": "4603"}}],
                               [{"name": "set_location", "input": {"zip": "46032", "city": "Carmel", "state": "in"}}],
                               [{"name": "search_nearby", "input": {"query": "physical therapist"}}],
                               reply("Here you go.")]))
    await handle_inbound(inbound("46032, Carmel"))
    user = await _user()
    assert places.label(user) == "Carmel, IN 46032"
    assert "physical+therapist+near+Carmel" in channel.sent[-1]["text"]


async def test_add_to_calendar_saves_the_appointment_links_and_two_reminders(channel):
    await consent(channel)
    tz = ZoneInfo(get_settings().clinic_timezone)
    start = (datetime.now(tz) + timedelta(days=3)).replace(hour=15, minute=30, second=0, microsecond=0)
    set_provider(FakeProvider([[{"name": "add_to_calendar", "input": {
        "provider_name": "Carmel Nutrition", "what": "Dietitian appointment",
        "start_local": f"{start:%Y-%m-%dT%H:%M}", "address": "123 Main St, Carmel"}}],
        reply("Done – it's in!")]))
    await handle_inbound(inbound("Yes please add it"))
    links = channel.sent[-1]["text"]
    async with session_scope() as db:
        appt = await db.scalar(select(Appointment))
        reminders = list(await db.scalars(select(Checkin).where(Checkin.appointment_id == appt.id)
                                          .order_by(Checkin.due_at)))
    assert (appt.source, appt.provider_slug, appt.location) == ("user_reported", "external", "123 Main St, Carmel")
    assert f"/cal/{appt.id}.ics" in links and "calendar.google.com/calendar/render?action=TEMPLATE" in links
    assert "day before and 2 hours before" in links
    assert len(reminders) == 2
    assert reminders[1].due_at.replace(tzinfo=None) == (start - timedelta(hours=2)).astimezone(
        ZoneInfo("UTC")).replace(tzinfo=None)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/cal/{appt.id}.ics")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
        assert "SUMMARY:Dietitian appointment – Carmel Nutrition" in r.text
        assert "LOCATION:123 Main St\\, Carmel" in r.text and "BEGIN:VALARM" in r.text
        assert (await c.get("/cal/not-a-uuid.ics")).status_code == 404


async def test_add_to_calendar_refuses_past_or_malformed_times(channel):
    await consent(channel)
    fake = FakeProvider([[{"name": "add_to_calendar", "input": {
        "provider_name": "X", "what": "Visit", "start_local": "2020-01-01T10:00"}}],
        [{"name": "add_to_calendar", "input": {"provider_name": "X", "what": "Visit", "start_local": "tomorrow 3pm"}}],
        reply("Which date and time is it?")])
    set_provider(fake)
    await handle_inbound(inbound("add my visit"))
    async with session_scope() as db:
        assert await db.scalar(select(Appointment)) is None
    tool_results = "\n".join(str(m.get("content", "")) for m in fake.calls[0].messages)
    assert "in_the_past" in tool_results and "bad_start" in tool_results


async def test_web_chat_send_location(monkeypatch):
    monkeypatch.setattr(places, "_transport", _geocoder([]))
    set_channel(RoutingChannel(ConsoleChannel(echo=False)))
    set_provider(FakeProvider([]))
    uid = "web123456789077"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"X-Demo-Key": get_settings().admin_token}) as c:
        await c.post("/chat/api/send", json={"user_id": uid, "text": "hi"})
        await asyncio.sleep(0.3)
        await c.post("/chat/api/send", json={"user_id": uid, "text": "I Agree", "button_id": "consent:yes"})
        await asyncio.sleep(0.3)
        set_provider(FakeProvider([reply("Thanks – got your area.")]))
        r = await c.post("/chat/api/send", json={"user_id": uid, "latitude": PIN[0], "longitude": PIN[1]})
        assert r.status_code == 200
        for _ in range(60):
            data = (await c.get("/chat/api/messages", params={"user_id": uid})).json()
            if any(m["body"].startswith("📍 Shared my location: Carmel") for m in data["messages"]):
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError(data)
        assert (await c.post("/chat/api/send", json={"user_id": uid, "latitude": 999, "longitude": 0})).status_code == 422
