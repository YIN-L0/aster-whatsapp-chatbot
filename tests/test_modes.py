"""One agent, mode-specific tools and context (docs/COMPANION_REDESIGN.md §4, phase C3)."""

from datetime import timedelta

from sqlalchemy import select

from app import agent, journeys
from app.context import build_context_block
from app.db import session_scope
from app.llm import FakeProvider, set_provider
from app.models import AgentRun, Handoff, Journey, ProviderLink, User
from app.pipeline import handle_inbound
from tests.conftest import inbound
from tests.test_flows import reply


async def _user() -> User:
    async with session_scope() as db:
        return await db.scalar(select(User))


async def _tool_outputs() -> list[dict]:
    async with session_scope() as db:
        run = (await db.scalars(select(AgentRun).where(AgentRun.kind == "main")
                                .order_by(AgentRun.created_at.desc()))).first()
        return [c["output"] for c in run.tool_calls]


async def companion_user(channel, first="hi"):
    set_provider(FakeProvider([]))
    await handle_inbound(inbound(first))
    await handle_inbound(inbound("I Agree", button_id="consent:yes"))
    await handle_inbound(inbound("No, my health goals", button_id="entry:direct"))


def test_prompt_is_companion_first_and_has_no_booking_push():
    p = agent.SYSTEM_PROMPT
    assert p.startswith("You are Aster, an independent AI health & longevity companion")
    assert "Be proactive" not in p
    assert "Never end an aftercare answer with a booking offer" in p
    assert "Don't mention providers, products or paid services unless they ask" in p


def test_booking_and_journey_tools_exist_only_in_treatment_mode():
    names = lambda mode: {t.name for t in agent.tools_for(_State(mode))}  # noqa: E731
    assert {"check_availability", "book_appointment", "propose_journey"} <= names("post_treatment")
    assert not {"check_availability", "book_appointment", "propose_journey", "record_recovery_update"} & names("companion")
    assert names("none") == names("companion")
    assert "switch_mode" in names("companion") and "switch_mode" in names("post_treatment")


class _State:
    def __init__(self, mode, allow=True):
        self.user = User(wa_id="1", active_mode=mode)
        self.allow = allow


async def test_companion_cannot_book_even_if_the_model_tries(channel):
    await companion_user(channel)
    set_provider(FakeProvider([
        [{"name": "check_availability", "input": {"purpose": "new_treatment", "service": "Botox"}}],
        reply("Happy to help with your goals."),
    ]))
    await handle_inbound(inbound("I want to lose a few pounds"))
    assert (await _tool_outputs())[0]["error"] == "tool_not_available"
    assert not any(m["type"] == "buttons" and "slot:" in str(m["buttons"]) for m in channel.sent)


async def test_switch_mode_needs_an_essence_client_then_unlocks_journey_tools_in_the_same_turn(channel):
    await companion_user(channel)
    user = await _user()
    yesterday = (journeys.local_today(user) - timedelta(days=1)).isoformat()
    set_provider(FakeProvider([
        [{"name": "switch_mode", "input": {"mode": "post_treatment"}}],
        [{"name": "switch_mode", "input": {"mode": "post_treatment", "essence_client_confirmed": True}}],
        [{"name": "propose_journey", "input": {"treatment_text": "Lip filler", "treatment_date": yesterday}}],
        reply("Got it – I'll save that once you confirm.", intents=["mode_switch", "journey_update"]),
    ]))
    await handle_inbound(inbound("Actually I had lip filler at Essence yesterday and it's swollen"))
    outs = await _tool_outputs()
    assert outs[0]["error"] == "not_an_essence_client"
    assert outs[1]["status"] == "switched" and "ACTIVE JOURNEY: none recorded yet" in outs[1]["context"]
    assert outs[2]["status"] == "awaiting_patient_confirmation"
    assert (await _user()).active_mode == "post_treatment"
    async with session_scope() as db:
        assert (await db.scalar(select(ProviderLink))).status == "self_reported"


async def test_context_shows_recovery_details_only_in_treatment_mode(channel):
    set_provider(FakeProvider([reply("Hi Emily! Checking in on your filler…", intents=["checkin"])]))
    await handle_inbound(inbound("hi"))
    await handle_inbound(inbound("I Agree", button_id="consent:yes"))
    await handle_inbound(inbound("#demo filler 2 Emily"))
    async with session_scope() as db:
        user = await db.scalar(select(User))
        journey = await db.scalar(select(Journey))
        treatment = await build_context_block(db, user, journey, None, [])
        user.active_mode = "companion"
        companion = await build_context_block(db, user, journey, None, [], returning=True)
    assert "PROTOCOL" in treatment and "SYMPTOM TIMELINE" in treatment
    assert "PROTOCOL" not in companion and "SYMPTOM TIMELINE" not in companion
    assert "essence treatment on file: Lip filler" in companion and "RETURNING" in companion


async def test_emergency_for_a_companion_user_alerts_aster_not_the_clinic(channel):
    await companion_user(channel)
    await handle_inbound(inbound("I have crushing chest pain and can't breathe"))
    texts = [m["text"] for m in channel.sent if m["type"] == "text" and m["to"] == "15551230000"]
    assert "911" in texts[-1] and "Aster care team" in texts[-1] and "555-0142" not in texts[-1]
    async with session_scope() as db:
        assert (await db.scalar(select(Handoff))).team == "aster"


async def test_emergency_during_a_recent_treatment_alerts_essence(channel):
    set_provider(FakeProvider([reply("Hi! Checking in…", intents=["checkin"])]))
    await handle_inbound(inbound("hi"))
    await handle_inbound(inbound("I Agree", button_id="consent:yes"))
    await handle_inbound(inbound("#demo filler 2 Emily"))
    await handle_inbound(inbound("My health goals", button_id="mode:companion"))  # switched away, treatment still recent
    await handle_inbound(inbound("my lip has a white patch and it's turning purple"))
    to_user = [m["text"] for m in channel.sent if m["type"] == "text" and m["to"] == "15551230000"]
    assert "911" in to_user[-1] and "555-0142" in to_user[-1]
    async with session_scope() as db:
        assert (await db.scalar(select(Handoff))).team == "essence"


async def test_agent_failure_in_companion_mode_uses_aster_wording(channel):
    await companion_user(channel)
    set_provider(FakeProvider([]))  # empty script → the LLM call fails
    await handle_inbound(inbound("What should I eat before a run?"))
    assert "Aster care team" in channel.sent[-1]["text"] and "555-0142" not in channel.sent[-1]["text"]


async def test_checkin_reply_is_in_treatment_mode_and_invites_to_the_companion_once(channel):
    set_provider(FakeProvider([reply("Hi Emily! How are your lips today?", intents=["checkin"])]))
    await handle_inbound(inbound("hi"))
    await handle_inbound(inbound("I Agree", button_id="consent:yes"))
    await handle_inbound(inbound("#demo filler 2 Emily"))
    async with session_scope() as db:
        (await db.scalar(select(User))).active_mode = "companion"  # the user wandered off before replying

    set_provider(FakeProvider([reply("Glad it's settling. If you notice blanching, call right away.",
                                     intents=["recovery_update", "checkin_wrap_up"])]))
    await handle_inbound(inbound("Swelling is down, no pain, no color changes"))
    assert (await _user()).active_mode == "post_treatment"
    invite = channel.sent[-1]
    assert invite["type"] == "buttons" and [b["id"] for b in invite["buttons"]] == ["mode:companion", "invite:no"]

    set_provider(FakeProvider([reply("Still looking good!", intents=["checkin_wrap_up"])]))
    await handle_inbound(inbound("all good today too"))
    assert channel.sent[-1]["text"] == "Still looking good!"  # once per journey

    await handle_inbound(inbound("Not now", button_id="invite:no"))
    assert "in the menu" in channel.sent[-1]["text"]


async def test_no_companion_invite_when_there_is_a_concern(channel):
    set_provider(FakeProvider([reply("Hi! How are you?", intents=["checkin"])]))
    await handle_inbound(inbound("hi"))
    await handle_inbound(inbound("I Agree", button_id="consent:yes"))
    await handle_inbound(inbound("#demo filler 2 Emily"))
    set_provider(FakeProvider([
        [{"name": "request_handoff", "input": {"reason": "risk_review", "urgency": "routine", "note_for_staff": "lumps"}}],
        reply("I've asked Claire to take a look.", risk=2, intents=["checkin_wrap_up", "handoff"]),
    ]))
    await handle_inbound(inbound("there's a hard lump that wasn't there yesterday"))
    assert not any(m["type"] == "buttons" and "invite:no" in str(m["buttons"]) for m in channel.sent)


async def test_choosing_the_companion_after_a_checkin_is_respected(channel):
    set_provider(FakeProvider([reply("Hi Emily! How are your lips today?", intents=["checkin"])]))
    await handle_inbound(inbound("hi"))
    await handle_inbound(inbound("I Agree", button_id="consent:yes"))
    await handle_inbound(inbound("#demo filler 2 Emily"))
    await handle_inbound(inbound("My health goals", button_id="mode:companion"))
    set_provider(FakeProvider([[{"name": "set_goal", "input": {"goal": "general"}}], reply("Let's talk food.")]))
    await handle_inbound(inbound("I want to eat better"))
    assert (await _user()).active_mode == "companion"
    assert (await _tool_outputs())[0]["status"] == "started"
