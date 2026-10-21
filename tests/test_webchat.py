import asyncio

import httpx

from app.channels import RoutingChannel, set_channel, ConsoleChannel
from app.config import get_settings
from app.llm import FakeProvider, set_provider
from app.main import app


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"X-Demo-Key": get_settings().admin_token})


async def _wait_for(client, user_id, predicate, timeout=3.0):
    for _ in range(int(timeout / 0.05)):
        data = (await client.get("/chat/api/messages", params={"user_id": user_id})).json()
        if predicate(data):
            return data
        await asyncio.sleep(0.05)
    raise AssertionError(f"condition not met: {data}")


async def test_webchat_onboarding_and_reply():
    set_channel(RoutingChannel(ConsoleChannel(echo=False)))
    uid = "web123456789001"
    async with _client() as c:
        assert (await c.get("/chat", params={"key": "wrong"})).status_code == 403
        assert (await c.get("/chat", params={"key": get_settings().admin_token})).status_code == 200

        await c.post("/chat/api/send", json={"user_id": uid, "name": "Emily", "text": "hi"})
        data = await _wait_for(c, uid, lambda d: any(m["type"] == "buttons" for m in d["messages"]))
        consent = next(m for m in data["messages"] if m["type"] == "buttons")
        assert [b["id"] for b in consent["buttons"]] == ["consent:yes", "consent:no"]

        await c.post("/chat/api/send", json={"user_id": uid, "name": "Emily", "text": "I Agree",
                                             "button_id": "consent:yes"})
        set_provider(FakeProvider([{"messages": ["Botox starts at $13/unit."], "risk_level": 0,
                                    "risk_reason": "", "intents": ["service_question"]}]))
        await c.post("/chat/api/send", json={"user_id": uid, "name": "Emily", "text": "How much is Botox?"})
        data = await _wait_for(c, uid, lambda d: any("$13" in m["body"] for m in d["messages"]) and not d["pending"])
        assert data["messages"][-1]["dir"] == "out"

        # Emergency path works in web chat too, and shows up for staff.
        await c.post("/chat/api/send", json={"user_id": uid, "name": "Emily", "text": "my vision is blurry"})
        await _wait_for(c, uid, lambda d: any("911" in m["body"] for m in d["messages"]))
        staff = (await c.get("/chat/api/staff")).json()
        assert staff["handoffs"][0]["urgency"] == "emergency"

        assert (await c.post("/chat/api/send", json={"user_id": "15551234567", "text": "x"})).status_code == 400
