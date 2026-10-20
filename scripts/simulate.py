"""Chat with Aster in the terminal – same pipeline as WhatsApp, no Meta account needed.

    .venv/bin/python -m scripts.simulate            # as patient +15550000001
    .venv/bin/python -m scripts.simulate --as 15550000002 --name Emily

Type a number to tap a button from the last button message. Type /quit to exit.
Uses the LLM configured in .env (LLM_PROVIDER, ANTHROPIC_API_KEY / OPENAI_API_KEY).
"""

import argparse
import asyncio
import itertools
import os
import sys

os.environ.setdefault("WA_ACCESS_TOKEN", "")  # force the console channel

from app.channels import ConsoleChannel, InboundMessage, set_channel  # noqa: E402
from app.db import init_db  # noqa: E402
from app.followups import run_due  # noqa: E402
from app.pipeline import handle_inbound  # noqa: E402


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--as", dest="wa_id", default="15550000001")
    p.add_argument("--name", default="Sarah")
    args = p.parse_args()

    channel = ConsoleChannel()
    set_channel(channel)
    await init_db()
    counter = itertools.count(1)
    print("Aster simulator – type a message (try '#help'). /quit to exit.\n")
    while True:
        try:
            text = await asyncio.to_thread(input, "👩 You: ")
        except EOFError:
            break
        if text.strip() == "/quit":
            break
        button = None
        last_buttons = next((m for m in reversed(channel.sent) if m["type"] == "buttons"), None)
        if text.strip().isdigit() and last_buttons:
            idx = int(text.strip()) - 1
            if 0 <= idx < len(last_buttons["buttons"]):
                button = last_buttons["buttons"][idx]
        msg = InboundMessage(
            wa_id=args.wa_id,
            wa_message_id=f"sim-{os.getpid()}-{next(counter)}",
            type="button_reply" if button else "text",
            text=button["title"] if button else text,
            profile_name=args.name,
            button_id=button["id"] if button else None,
        )
        await handle_inbound(msg)
        from app.db import session_scope

        async with session_scope() as db:
            await run_due(db)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
