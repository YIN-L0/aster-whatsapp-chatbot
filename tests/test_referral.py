"""Need-driven provider referral (docs/COMPANION_REDESIGN.md §6.4, phase C5)."""

from datetime import timedelta

from sqlalchemy import select

from app import safety
from app.db import session_scope
from app.llm import FakeProvider, set_provider
from app.models import AgentRun, Handoff, ReferralOffer, User, utcnow
from app.pipeline import handle_inbound
from tests.conftest import inbound
from tests.test_assessment_flow import call, companion_user
from tests.test_flows import reply


async def _outputs() -> list[dict]:
    async with session_scope() as db:
        run = (await db.scalars(select(AgentRun).where(AgentRun.kind == "main")
                                .order_by(AgentRun.created_at.desc()))).first()
        return [c["output"] for c in run.tool_calls]


async def _offers() -> list[tuple[str, str]]:
    async with session_scope() as db:
        return [(o.need, o.status) for o in await db.scalars(select(ReferralOffer).order_by(ReferralOffer.created_at))]


def _to_user(channel) -> list[dict]:
    return [m for m in channel.sent if m["to"] == "15551230000"]


async def test_options_need_a_request_from_the_user(channel):
    await companion_user(channel)
    set_provider(FakeProvider([call("find_services", need="s_dietitian"), reply("Some tips for afternoon energy…")]))
    await handle_inbound(inbound("I always feel tired after lunch"))
    assert (await _outputs())[0]["error"] == "user_has_not_asked"
    assert await _offers() == []


async def test_offer_then_yes_shows_neutral_options_with_a_disclosure_from_code(channel):
    await companion_user(channel)
    set_provider(FakeProvider([call("offer_options", need="s_dietitian"),
                               reply("A dietitian could help you sort this out. Would you like some options?")]))
    await handle_inbound(inbound("I've tried every diet and nothing sticks"))
    buttons = _to_user(channel)[-1]
    assert buttons["type"] == "buttons" and buttons["buttons"][0]["id"].startswith("ref:yes:")

    fake = FakeProvider([call("find_services", need="s_dietitian"),
                         reply("First, a tip… One option is Helix & Harvest's dietitian review, online. "
                               "Your doctor can also refer you to a local dietitian.")])
    set_provider(fake)
    await handle_inbound(inbound("Yes, show options", button_id=buttons["buttons"][0]["id"]))
    said = [m["content"] for m in fake.calls[0].messages if m["role"] == "user"][-1]
    assert "[accepted options for need=s_dietitian]" in said
    out = (await _outputs())[0]
    assert out["options"][0]["provider"] == "helix_harvest" and out["neutral"]
    assert "disclosure" not in out["options"][0]  # the model never writes it…
    texts = [m["text"] for m in _to_user(channel)]
    assert texts[-1].startswith("ℹ️ Aster partners with Helix & Harvest")  # …code sends it
    assert await _offers() == [("s_dietitian", "accepted"), ("s_dietitian", "options_shown")]


async def test_no_thanks_blocks_offers_for_30_days_but_asking_still_works(channel):
    await companion_user(channel)
    set_provider(FakeProvider([call("offer_options", need="s_fitness"), reply("Would you like some options?")]))
    await handle_inbound(inbound("I never know what to do at the gym"))
    no = _to_user(channel)[-1]["buttons"][1]
    await handle_inbound(inbound("No thanks", button_id=no["id"]))
    assert "here whenever" in _to_user(channel)[-1]["text"]
    assert await _offers() == [("s_fitness", "declined")]

    set_provider(FakeProvider([call("offer_options", need="s_fitness"), reply("Here are some beginner moves…")]))
    await handle_inbound(inbound("what should I do on leg day?"))
    assert (await _outputs())[0]["error"] == "declined_recently"
    assert _to_user(channel)[-1]["type"] == "text"  # no new offer buttons

    set_provider(FakeProvider([call("find_services", need="s_fitness"), reply("Sure – here are some options…")]))
    await handle_inbound(inbound("Actually, can you recommend a trainer?"))
    assert (await _outputs())[0]["options"]

    async with session_scope() as db:  # after the cooldown, offers are possible again
        for o in await db.scalars(select(ReferralOffer)):
            o.created_at = utcnow() - timedelta(days=31)
    set_provider(FakeProvider([call("offer_options", need="s_fitness"), reply("Would you like some options?")]))
    await handle_inbound(inbound("still lost at the gym"))
    assert (await _outputs())[0]["status"] == "offered"


async def test_typed_decline_is_recorded_and_shown_in_the_context(channel):
    await companion_user(channel)
    fake = FakeProvider([call("decline_options", need="s_gut_eval"), reply("No problem.")])
    set_provider(fake)
    await handle_inbound(inbound("no, I don't want to see anyone about it"))
    set_provider(fake := FakeProvider([reply("ok")]))
    await handle_inbound(inbound("anyway"))
    said = [m["content"] for m in fake.calls[0].messages if m["role"] == "user"][-1]
    assert "REFERRALS: they said no to options for s_gut_eval" in said


async def test_find_a_service_in_the_menu_counts_as_asking(channel):
    await companion_user(channel)
    await handle_inbound(inbound("Find a service", button_id="menu:services"))
    set_provider(FakeProvider([call("find_services", need="s_gut_eval"), reply("Here's what I'd suggest…")]))
    await handle_inbound(inbound("help with bloating"))  # no "recommend" words – the menu tap was the request
    out = (await _outputs())[0]
    assert "options" in out and "primary care" in out["neutral"]


async def test_caution_hides_weight_loss_services_for_a_teen(channel):
    await companion_user(channel)
    set_provider(FakeProvider([
        call("record_profile_facts", facts={"age": {"age": 16}}),
        call("find_services", need="s_medical_weight", us_state="IN"),
        reply("Let's start with your doctor."),
    ]))
    await handle_inbound(inbound("I'm 16, can you recommend a weight loss clinic in Indiana?"))
    out = (await _outputs())[1]
    assert out["options"] == [] and "primary care" in out["neutral"]


async def test_unrequested_provider_mention_is_blocked_and_rewritten(channel):
    await companion_user(channel)
    set_provider(FakeProvider([
        reply("Try more protein. Essence also has a great weight program!"),
        reply("Try a palm-size protein portion at each meal."),
    ]))
    await handle_inbound(inbound("how much protein should I eat?"))
    assert _to_user(channel)[-1]["text"] == "Try a palm-size protein portion at each meal."
    async with session_scope() as db:
        run = await db.scalar(select(AgentRun).where(AgentRun.kind == "main"))
        assert "unrequested_provider" in run.postcheck_violations


def test_postcheck_provider_names_are_case_sensitive_and_respect_allowed():
    assert safety.postcheck("The essence of it is sleep.", booked_this_turn=False, allowed_providers=set()) == []
    assert safety.postcheck("Call Essence.", booked_this_turn=False, allowed_providers=set()) == ["unrequested_provider"]
    assert safety.postcheck("Call Essence.", booked_this_turn=False, allowed_providers={"essence"}) == []


async def test_essence_clients_may_hear_about_essence(channel):
    await companion_user(channel)
    await handle_inbound(inbound("I'm an Essence client", button_id="entry:essence"))
    await handle_inbound(inbound("My health goals", button_id="mode:companion"))
    set_provider(FakeProvider([reply("Your Essence provider can tell you more about that.")]))
    await handle_inbound(inbound("is it ok to work out after my facial?"))
    assert _to_user(channel)[-1]["text"] == "Your Essence provider can tell you more about that."


async def test_sharing_needs_a_tap_and_sends_only_the_summary(channel):
    set_provider(FakeProvider([reply("Hi! Checking in…", intents=["checkin"])]))
    await handle_inbound(inbound("hi"))
    await handle_inbound(inbound("I Agree", button_id="consent:yes"))
    await handle_inbound(inbound("#demo filler 2 Emily"))  # an Essence journey with symptoms on file
    await handle_inbound(inbound("My health goals", button_id="mode:companion"))
    set_provider(FakeProvider([
        call("set_goal", goal="general", in_their_words="eat better"),
        call("share_with_provider", provider="helix_harvest", need="s_dietitian"),
        reply("Want me to send them a short summary?"),
    ]))
    await handle_inbound(inbound("Can you recommend a dietitian? I want to eat better. Helix sounds good"))
    ask = _to_user(channel)[-1]
    assert ask["type"] == "buttons" and "Looking for: Registered dietitian" in ask["text"]
    async with session_scope() as db:
        assert await db.scalar(select(Handoff).where(Handoff.reason == "referral_share")) is None  # nothing yet

    await handle_inbound(inbound("Share my summary", button_id=ask["buttons"][0]["id"]))
    assert "(414) 555-0187" in _to_user(channel)[-1]["text"]
    async with session_scope() as db:
        h = await db.scalar(select(Handoff).where(Handoff.reason == "referral_share"))
    assert h.team == "helix_harvest" and h.status == "sent"
    assert "Goal: eat better" in h.summary
    assert "filler" not in h.summary.lower() and "swelling" not in h.summary.lower()  # no Essence data crosses over
    staff = [m["text"] for m in channel.sent if m["to"] == "15559990000"]
    assert staff[-1].startswith("📨 Referral from Aster")

    await handle_inbound(inbound("Don't share", button_id="ref:noshare"))
    assert "nothing was shared" in _to_user(channel)[-1]["text"]
