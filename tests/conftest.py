import os
import tempfile

_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{_db.name}")
os.environ["WA_ACCESS_TOKEN"] = ""
os.environ["WA_APP_SECRET"] = ""
os.environ["STAFF_WHATSAPP_NUMBERS"] = "15559990000"
os.environ["DEMO_MODE"] = "true"

import itertools  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.channels import ConsoleChannel, InboundMessage, set_channel  # noqa: E402
from app.db import Base, engine, init_db  # noqa: E402
from app.llm import set_provider  # noqa: E402

_ids = itertools.count(1)


@pytest_asyncio.fixture(autouse=True)
async def fresh_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await init_db()
    yield
    set_provider(None)


@pytest.fixture
def channel():
    ch = ConsoleChannel(echo=False)
    set_channel(ch)
    return ch


def inbound(text: str = "", wa_id: str = "15551230000", button_id: str | None = None, name: str = "Emily"):
    return InboundMessage(
        wa_id=wa_id, wa_message_id=f"wamid.test{next(_ids)}", type="button_reply" if button_id else "text",
        text=text, profile_name=name, button_id=button_id,
    )
