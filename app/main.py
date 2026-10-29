import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import text, update

from app import admin, booking, webchat
from app.channels import get_channel, parse_webhook, verify_signature
from app.config import get_settings
from app.db import init_db, session_scope
from app.followups import scheduler_loop
from app.models import Message
from app.pipeline import ingest, process_user

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("aster")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    stop = asyncio.Event()
    task = asyncio.create_task(scheduler_loop(stop))
    asyncio.create_task(_warm_booking_catalog())
    log.info("Aster started (whatsapp=%s, llm=%s)", get_settings().whatsapp_enabled, get_settings().llm_provider)
    yield
    stop.set()
    await task


async def _warm_booking_catalog() -> None:
    """Load doctors and services once at startup so the first patient who books doesn't wait for it."""
    api = booking.api_adapter()
    if api:
        try:
            await api.catalog()
        except Exception as exc:  # the first booking will simply load it instead
            log.warning("booking catalog warm-up failed: %s", exc)


app = FastAPI(title="Aster WhatsApp demo", lifespan=lifespan)
app.include_router(webchat.router)


@app.get("/healthz")
async def healthz() -> dict:
    async with session_scope() as db:
        await db.execute(text("select 1"))
    return {"ok": True}


@app.get("/webhooks/whatsapp")
async def verify(
    mode: str = Query(alias="hub.mode", default=""),
    token: str = Query(alias="hub.verify_token", default=""),
    challenge: str = Query(alias="hub.challenge", default=""),
):
    if mode == "subscribe" and token == get_settings().wa_verify_token:
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403)


@app.post("/webhooks/whatsapp")
async def receive(request: Request, background: BackgroundTasks):
    raw = await request.body()
    if not verify_signature(raw, request.headers.get("X-Hub-Signature-256"), get_settings().wa_app_secret):
        raise HTTPException(status_code=401)
    messages, statuses = parse_webhook(await request.json())
    users = []
    last_msg: dict[str, str] = {}
    for m in messages:
        uid = await ingest(m)
        if uid and uid not in users:
            users.append(uid)
        if uid and m.wa_message_id:
            last_msg[m.wa_id] = m.wa_message_id
    if statuses:
        async with session_scope() as db:
            for s in statuses:
                await db.execute(
                    update(Message).where(Message.wa_message_id == s.get("id")).values(delivery_status=s.get("status"))
                )
    for wa_id, message_id in last_msg.items():
        background.add_task(get_channel().show_typing, wa_id, message_id)  # patient sees "typing…" while we think
    for uid in users:
        background.add_task(process_user, uid)  # reply after returning 200 to Meta
    return {"ok": True}


@app.get("/demo", response_class=HTMLResponse)
async def demo_dashboard(key: str = ""):
    if key != get_settings().admin_token:
        raise HTTPException(status_code=403)
    async with session_scope() as db:
        return HTMLResponse(await admin.render(db, key))
