"""Channel adapters. Only this module knows about the WhatsApp Graph API."""

import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)


@dataclass
class Button:
    id: str
    title: str  # WhatsApp limit: 20 chars


@dataclass
class InboundMessage:
    wa_id: str
    wa_message_id: str
    type: str  # text | button_reply | media | unsupported
    text: str
    profile_name: str | None = None
    button_id: str | None = None


@dataclass
class SendResult:
    ok: bool
    wa_message_id: str | None = None
    error: str | None = None


class Channel(Protocol):
    async def send_text(self, to: str, text: str) -> SendResult: ...

    async def send_buttons(self, to: str, body: str, buttons: list[Button]) -> SendResult: ...

    async def send_template(self, to: str, name: str, params: list[str]) -> SendResult: ...


class WhatsAppChannel:
    def __init__(self) -> None:
        s = get_settings()
        self._url = f"https://graph.facebook.com/{s.wa_graph_version}/{s.wa_phone_number_id}/messages"
        self._headers = {"Authorization": f"Bearer {s.wa_access_token}"}
        self._lang = s.wa_template_lang
        self._client = httpx.AsyncClient(timeout=15)

    async def _post(self, payload: dict) -> SendResult:
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual", **payload}
        for attempt in range(3):
            try:
                resp = await self._client.post(self._url, json=payload, headers=self._headers)
            except httpx.HTTPError as exc:
                log.warning("whatsapp send network error attempt=%s err=%s", attempt, exc)
                continue
            if resp.status_code == 200:
                return SendResult(ok=True, wa_message_id=resp.json()["messages"][0]["id"])
            if resp.status_code in (429, 500, 502, 503) and attempt < 2:
                continue
            log.error("whatsapp send failed status=%s body=%s", resp.status_code, resp.text[:500])
            return SendResult(ok=False, error=resp.text[:500])
        return SendResult(ok=False, error="network_error")

    async def send_text(self, to: str, text: str) -> SendResult:
        return await self._post({"to": to, "type": "text", "text": {"body": text[:4096], "preview_url": False}})

    async def send_buttons(self, to: str, body: str, buttons: list[Button]) -> SendResult:
        return await self._post(
            {
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body[:1024]},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": b.id, "title": b.title[:20]}} for b in buttons[:3]
                        ]
                    },
                },
            }
        )

    async def send_template(self, to: str, name: str, params: list[str]) -> SendResult:
        template: dict = {"name": name, "language": {"code": self._lang}}
        if params:
            template["components"] = [
                {"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}
            ]
        return await self._post({"to": to, "type": "template", "template": template})


@dataclass
class ConsoleChannel:
    """Used by scripts/simulate.py and tests. Records everything it 'sends'."""

    echo: bool = True
    sent: list[dict] = field(default_factory=list)

    async def send_text(self, to: str, text: str) -> SendResult:
        self.sent.append({"to": to, "type": "text", "text": text})
        if self.echo:
            print(f"\n🌸 Aster → {to}:\n{text}\n")
        return SendResult(ok=True, wa_message_id=None)

    async def send_buttons(self, to: str, body: str, buttons: list[Button]) -> SendResult:
        self.sent.append({"to": to, "type": "buttons", "text": body, "buttons": [b.__dict__ for b in buttons]})
        if self.echo:
            opts = "  ".join(f"[{i + 1}] {b.title}" for i, b in enumerate(buttons))
            print(f"\n🌸 Aster → {to}:\n{body}\n{opts}\n")
        return SendResult(ok=True)

    async def send_template(self, to: str, name: str, params: list[str]) -> SendResult:
        self.sent.append({"to": to, "type": "template", "name": name, "params": params})
        if self.echo:
            print(f"\n🌸 Aster → {to}: <template {name} {params}>\n")
        return SendResult(ok=True)


WEB_PREFIX = "web"


def is_web_user(wa_id: str) -> bool:
    return wa_id.startswith(WEB_PREFIX)


class WebChannel:
    """Web chat (app/static/chat.html). Messages are already persisted by app.outbound; the page polls them."""

    async def send_text(self, to: str, text: str) -> SendResult:
        return SendResult(ok=True)

    async def send_buttons(self, to: str, body: str, buttons: list[Button]) -> SendResult:
        return SendResult(ok=True)

    async def send_template(self, to: str, name: str, params: list[str]) -> SendResult:
        return SendResult(ok=True)


class RoutingChannel:
    """Sends web-chat users to WebChannel and everyone else to WhatsApp (or the console in local dev)."""

    def __init__(self, primary: Channel) -> None:
        self.primary = primary
        self.web = WebChannel()

    def _for(self, to: str) -> Channel:
        return self.web if is_web_user(to) else self.primary

    async def send_text(self, to: str, text: str) -> SendResult:
        return await self._for(to).send_text(to, text)

    async def send_buttons(self, to: str, body: str, buttons: list[Button]) -> SendResult:
        return await self._for(to).send_buttons(to, body, buttons)

    async def send_template(self, to: str, name: str, params: list[str]) -> SendResult:
        return await self._for(to).send_template(to, name, params)


_channel: Channel | None = None


def get_channel() -> Channel:
    global _channel
    if _channel is None:
        primary = WhatsAppChannel() if get_settings().whatsapp_enabled else ConsoleChannel()
        _channel = RoutingChannel(primary)
    return _channel


def set_channel(channel: Channel) -> None:
    global _channel
    _channel = channel


# ---------- webhook parsing ----------


def verify_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
    if not app_secret:
        # Allowed only for local development; production must set WA_APP_SECRET.
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


def parse_webhook(payload: dict) -> tuple[list[InboundMessage], list[dict]]:
    """Returns (messages, statuses) from a WhatsApp webhook payload."""
    messages: list[InboundMessage] = []
    statuses: list[dict] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}
            for m in value.get("messages", []):
                wa_id = m.get("from", "")
                mtype = m.get("type")
                text, button_id, kind = "", None, "unsupported"
                if mtype == "text":
                    kind, text = "text", m.get("text", {}).get("body", "")
                elif mtype == "interactive":
                    inter = m.get("interactive", {})
                    reply = inter.get("button_reply") or inter.get("list_reply") or {}
                    kind, text, button_id = "button_reply", reply.get("title", ""), reply.get("id")
                elif mtype == "button":  # quick-reply button on a template
                    kind = "button_reply"
                    text = m.get("button", {}).get("text", "")
                    button_id = m.get("button", {}).get("payload")
                elif mtype in ("image", "audio", "video", "document", "sticker"):
                    kind = "media"
                elif mtype == "reaction":
                    continue
                messages.append(
                    InboundMessage(
                        wa_id=wa_id,
                        wa_message_id=m.get("id", ""),
                        type=kind,
                        text=text,
                        profile_name=names.get(wa_id),
                        button_id=button_id,
                    )
                )
            statuses.extend(value.get("statuses", []))
    return messages, statuses
