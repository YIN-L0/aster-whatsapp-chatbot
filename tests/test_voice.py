"""Voice notes: WhatsApp audio -> transcript -> the normal text pipeline."""

import httpx
from sqlalchemy import select

from app import voice
from app.channels import InboundMessage, parse_webhook
from app.db import session_scope
from app.llm import FakeProvider, set_provider
from app.models import Message
from app.pipeline import handle_inbound
from tests.conftest import inbound
from tests.test_location_calendar import consent, reply


def _voice(wa_id="15551230000", media_id="media123"):
    return InboundMessage(wa_id=wa_id, wa_message_id=f"wamid.v{media_id}", type="voice", text="", media_id=media_id)


def test_audio_message_is_parsed_as_voice():
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "1555", "id": "wamid.a", "type": "audio", "audio": {"id": "m1", "mime_type": "audio/ogg; codecs=opus"}}]}}]}]}
    (msg,), _ = parse_webhook(payload)
    assert (msg.type, msg.media_id) == ("voice", "m1")


async def test_voice_note_is_answered_like_typed_text(channel, monkeypatch):
    await consent(channel)
    seen = []

    async def fake(media_id):
        seen.append(media_id)
        return "我最近睡得不好"

    monkeypatch.setattr(voice, "transcribe_media", fake)
    set_provider(FakeProvider([reply("Sorry to hear that.")]))
    await handle_inbound(_voice())
    assert seen == ["media123"]
    assert channel.sent[-1]["text"] == "Sorry to hear that."
    async with session_scope() as db:
        m = await db.scalar(select(Message).where(Message.wa_message_id == "wamid.vmedia123"))
    assert (m.type, m.body, m.payload["source"]) == ("voice", "我最近睡得不好", "voice")


async def test_unintelligible_voice_note_asks_to_resend(channel, monkeypatch):
    await consent(channel)

    async def nothing(media_id):
        return None

    monkeypatch.setattr(voice, "transcribe_media", nothing)
    await handle_inbound(_voice())
    assert "send it again" in channel.sent[-1]["text"]


async def test_no_audio_leaves_before_consent(channel, monkeypatch):
    async def boom(media_id):
        raise AssertionError("must not transcribe before consent")

    monkeypatch.setattr(voice, "transcribe_media", boom)
    set_provider(FakeProvider([]))
    await handle_inbound(inbound("hi"))
    await handle_inbound(_voice())


async def test_download_checks_mime_and_size(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "graph.facebook.com":
            mime = "audio/ogg; codecs=opus" if req.url.path.endswith("good") else "audio/aac"
            return httpx.Response(200, json={"url": "https://cdn.example/a", "mime_type": mime, "file_size": 10})
        assert req.headers["Authorization"].startswith("Bearer ")
        return httpx.Response(200, content=b"OggS")

    real = httpx.AsyncClient
    monkeypatch.setattr(voice.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    assert await voice.download("good") == (b"OggS", "audio/ogg")
    assert await voice.download("bad") is None


async def test_voice_note_sent_before_consent_is_answered_after_consent(channel, monkeypatch):
    calls = []

    async def fake(media_id):
        calls.append(media_id)
        return "I want to talk about my sleep and energy levels"

    monkeypatch.setattr(voice, "transcribe_media", fake)
    set_provider(FakeProvider([reply("Let's look at your sleep.")]))
    await handle_inbound(inbound("hi"))
    await handle_inbound(_voice())
    assert calls == []  # nothing is sent to OpenAI before consent
    await handle_inbound(inbound("I Agree", button_id="consent:yes"))
    assert calls == ["media123"]
    assert channel.sent[-1]["text"] == "Let's look at your sleep."


async def test_transcribe_media_never_raises(monkeypatch):
    async def boom(media_id):
        raise RuntimeError("x")

    monkeypatch.setattr(voice, "download", boom)
    assert await voice.transcribe_media("m") is None
