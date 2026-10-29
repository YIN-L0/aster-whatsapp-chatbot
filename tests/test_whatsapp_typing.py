import json

import httpx

from app.channels import ConsoleChannel, RoutingChannel, WhatsAppChannel, set_channel
from app.main import app


async def test_show_typing_marks_read_with_typing_indicator():
    seen = []
    ch = WhatsAppChannel()
    ch._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: seen.append(json.loads(req.content)) or httpx.Response(200, json={"success": True})))
    await ch.show_typing("15551230000", "wamid.abc")
    assert seen == [{"messaging_product": "whatsapp", "status": "read", "message_id": "wamid.abc",
                     "typing_indicator": {"type": "text"}}]


async def test_show_typing_failure_is_swallowed():
    ch = WhatsAppChannel()
    ch._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(400, text="bad")))
    await ch.show_typing("15551230000", "wamid.abc")  # must not raise


async def test_webhook_shows_typing_for_last_message():
    calls = []

    class Spy(ConsoleChannel):
        async def show_typing(self, to, message_id):
            calls.append((to, message_id))

    set_channel(RoutingChannel(Spy(echo=False)))
    msgs = [{"from": "15551230000", "id": f"wamid.{i}", "type": "text", "text": {"body": "hi"}} for i in (1, 2)]
    payload = {"entry": [{"changes": [{"value": {"contacts": [], "messages": msgs}}]}]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.post("/webhooks/whatsapp", json=payload)).status_code == 200
    assert calls == [("15551230000", "wamid.2")]
