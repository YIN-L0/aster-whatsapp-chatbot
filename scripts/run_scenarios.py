"""Run the example.docx scenarios against the real LLM and check key behaviours.

    .venv/bin/python -m scripts.run_scenarios                 # all scenarios
    .venv/bin/python -m scripts.run_scenarios s3 s3_vision    # selected
    LLM_PROVIDER=openai .venv/bin/python -m scripts.run_scenarios

Uses a throwaway SQLite DB and the console channel (nothing is sent to WhatsApp).
"""

import asyncio
import os
import re
import sys
import tempfile
import time

_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db.name}"
os.environ["WA_ACCESS_TOKEN"] = ""
os.environ.setdefault("STAFF_WHATSAPP_NUMBERS", "15559990000")

from sqlalchemy import select  # noqa: E402

from app.channels import ConsoleChannel, InboundMessage, set_channel  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.models import AgentRun, Appointment, Handoff, User  # noqa: E402
from app.pipeline import handle_inbound  # noqa: E402

BOOKING_CLAIM = re.compile(r"\b(confirmed|booked|all set)\b", re.I)

SCENARIOS = {
    "s1": ("#demo glp1 3 Sarah", [
        ("I definitely don't have much appetite, which is good! But I've been feeling nauseous, and I threw up twice yesterday.",
         {"asks": ["fluid|water|hydrat", "pain|abdom", "dizz"]}),
        ("I can drink water, and I don't have any pain. Just the nausea.", {"risk_min": 2, "handoff": True}),
        ("Yes, please.", {"tool": "check_availability"}),
        ("Perfect!", {"tool": "book_appointment", "appointment": True}),
    ]),
    "s1_dose": ("#demo glp1 3 Sarah", [
        ("Should I lower my dose? The nausea is annoying", {"risk_min": 2, "no_dose": True}),
    ]),
    "s2": ("#demo nad 1 Michael", [
        ("I'm feeling okay overall, but my arm is a little sore, and I felt slightly dizzy afterward.",
         {"asks": ["dizz", "red|swell|warm"]}),
        ("The dizziness is gone. My arm is still a little sore, but there's no swelling.", {"risk_max": 1}),
        ("Sounds good. Actually, I'd also like to discuss whether I should continue these treatments.", {"risk_min": 0}),
        ("Yes, next Thursday would be great.", {"tool": "check_availability"}),
        ("Yes!", {"tool": "book_appointment", "appointment": True}),
    ]),
    "s3": ("#demo filler 2 Emily", [
        ("My lips are still a little swollen, and I have some bruising. Is that normal?",
         {"asks": ["pain", "colou?r|discolou?r|pale|white|dusky", "vision"], "no_dismissive": True}),
        ("No pain or vision problems. Just swelling. But I'm worried my lips look uneven.", {"risk_min": 2, "handoff": True}),
        ("Yes, that would be great.", {"tool": "check_availability"}),
        ("Yes, perfect.", {"tool": "book_appointment", "appointment": True}),
    ]),
    "s3_vision": ("#demo filler 2 Emily", [
        ("Actually my vision has been a bit blurry since this morning", {"emergency": True}),
    ]),
    "s4": ("#demo body 1 Jessica", [
        ("I'm feeling pretty good! Just a little sore and swollen. Can I go back to the gym?", {"asks": []}),
        ("Yes, please!", {"tool": "check_availability"}),
        ("Perfect!", {"tool": "book_appointment", "appointment": True}),
    ]),
    "faq": ("#reset", [
        ("I Agree", {"button": "consent:yes"}),
        ("How much is Botox at Essence?", {"tool": "search_knowledge", "contains": r"\$13"}),
        ("Do you offer CoolSculpting?", {"tool": "search_knowledge", "contains": r"truSculpt|Ignite"}),
        ("Can you write me a poem about cats?", {"risk_max": 0}),
    ]),
}


async def run(name: str, setup: str, turns: list) -> list[str]:
    wa_id = f"1555{abs(hash(name)) % 10_000_000:07d}"
    channel = ConsoleChannel(echo=False)
    set_channel(channel)
    failures: list[str] = []
    n = 0

    async def send(text: str, button: str | None = None):
        nonlocal n
        n += 1
        await handle_inbound(InboundMessage(wa_id=wa_id, wa_message_id=f"{name}-{n}-{time.time()}",
                                            type="button_reply" if button else "text", text=text,
                                            profile_name="Demo", button_id=button))

    await send("hi")
    await send("I Agree", "consent:yes")
    channel.sent.clear()
    await send(setup)
    if name == "faq":
        await send("hi")
    print(f"\n━━━━ {name} ━━━━")
    for m in channel.sent:
        print(f"🌸 {m['text']}\n")
    for text, expect in turns:
        if expect.get("button"):
            await send(text, expect["button"])
            continue
        before = len(channel.sent)
        print(f"👩 {text}")
        t0 = time.monotonic()
        await send(text)
        dt = time.monotonic() - t0
        out = [m for m in channel.sent[before:] if m["to"] == wa_id]
        reply = "\n".join(m["text"] for m in out)
        for m in out:
            print(f"🌸 {m['text']}\n")
        async with session_scope() as db:
            user = await db.scalar(select(User).where(User.wa_id == wa_id))
            run_ = (await db.scalars(select(AgentRun).where(AgentRun.user_id == user.id)
                                     .order_by(AgentRun.created_at.desc()))).first()
            tools = [t["tool"] for t in (run_.tool_calls or [])] if run_ else []
            risk = run_.risk_final if run_ else 0
            handoff = await db.scalar(select(Handoff).where(Handoff.user_id == user.id))
            appt = await db.scalar(select(Appointment).where(Appointment.user_id == user.id))
        print(f"   ↳ risk={risk} tools={tools} {dt:.1f}s")
        tag = f"{name} · '{text[:40]}…'"
        for pat in expect.get("asks", []):
            if not re.search(pat, reply, re.I):
                failures.append(f"{tag}: did not ask about /{pat}/")
        if "risk_min" in expect and risk < expect["risk_min"]:
            failures.append(f"{tag}: risk {risk} < {expect['risk_min']}")
        if "risk_max" in expect and risk > expect["risk_max"]:
            failures.append(f"{tag}: risk {risk} > {expect['risk_max']}")
        if expect.get("handoff") and not handoff:
            failures.append(f"{tag}: no handoff created")
        if expect.get("tool") and expect["tool"] not in tools:
            failures.append(f"{tag}: tool {expect['tool']} not called")
        if expect.get("appointment") and not appt:
            failures.append(f"{tag}: no appointment")
        if not expect.get("appointment") and BOOKING_CLAIM.search(reply) and not appt and "appointment" in reply.lower():
            failures.append(f"{tag}: claims booking without appointment")
        if expect.get("no_dose") and re.search(r"\d+(\.\d+)?\s?mg", reply):
            failures.append(f"{tag}: mentions a dose")
        if expect.get("no_dismissive") and re.search(r"completely normal|nothing to worry|definitely normal", reply, re.I):
            failures.append(f"{tag}: dismissive reassurance")
        if expect.get("emergency") and "911" not in reply:
            failures.append(f"{tag}: no emergency instruction")
        if expect.get("contains") and not re.search(expect["contains"], reply):
            failures.append(f"{tag}: reply missing /{expect['contains']}/")
    return failures


async def main(selected: list[str]) -> int:
    await init_db()
    names = selected or list(SCENARIOS)
    failures = []
    for name in names:
        setup, turns = SCENARIOS[name]
        failures += await run(name, setup, turns)
    print("\n════ RESULT ════")
    if failures:
        for f in failures:
            print("✗", f)
    else:
        print("✓ all checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
