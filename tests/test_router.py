"""Entry sources, menu, mode switching and resume (docs/COMPANION_REDESIGN.md §5–7, phase C2)."""

from datetime import timedelta

from sqlalchemy import select

from app.channels import parse_webhook
from app.db import session_scope
from app.llm import FakeProvider, set_provider
from app.models import CompanionAssessment, Handoff, ProviderLink, User, utcnow
from app.pipeline import handle_inbound
from tests.conftest import inbound


def reply(*messages):
    return {"messages": list(messages), "risk_level": 0, "risk_reason": "", "intents": ["smalltalk"]}


async def _user() -> User:
    async with session_scope() as db:
        return await db.scalar(select(User))


async def _links() -> dict[str, str]:
    async with session_scope() as db:
        return {link.provider_slug: link.status for link in await db.scalars(select(ProviderLink))}


def _ids(msg) -> list[str]:
    return [b["id"] for b in msg.get("buttons") or msg.get("rows") or []]


async def consent(channel, first="hi"):
    set_provider(FakeProvider([]))  # nothing in this flow may call the LLM
    await handle_inbound(inbound(first))
    await handle_inbound(inbound("I Agree", button_id="consent:yes"))


# ---------- entry ----------


async def test_direct_user_gets_the_essence_question_once_and_lands_in_the_companion(channel):
    await consent(channel)
    welcome = next(m for m in channel.sent if m["type"] == "text")
    assert "health & longevity companion" in welcome["text"]
    triage = channel.sent[-1]
    assert _ids(triage) == ["entry:essence", "entry:direct", "entry:skip"]
    assert all(len(b["title"]) <= 20 for b in triage["buttons"])

    await handle_inbound(inbound("No, my health goals", button_id="entry:direct"))
    assert "Your health companion" in channel.sent[-2]["text"]
    goals = channel.sent[-1]
    assert goals["type"] == "list" and _ids(goals)[0] == "goal:lose_weight" and len(goals["rows"]) <= 10
    assert all(len(r["title"]) <= 24 for r in goals["rows"])
    user = await _user()
    assert (user.entry_source, user.active_mode) == ("direct", "companion")
    assert await _links() == {}


async def test_essence_code_skips_the_question_and_still_lets_the_user_choose(channel):
    await consent(channel, first="Hi Aster (ESS)")
    assert not any("entry:essence" in _ids(m) for m in channel.sent)
    assert _ids(channel.sent[-1]) == ["mode:post_treatment", "mode:companion"]
    assert await _links() == {"essence": "referred"}
    assert (await _user()).entry_source == "essence"

    await handle_inbound(inbound("My treatment", button_id="mode:post_treatment"))
    assert "Which treatment did you have at Essence" in channel.sent[-1]["text"]
    assert (await _user()).active_mode == "post_treatment"


async def test_other_provider_users_are_never_treated_as_essence_clients(channel):
    await consent(channel, first="hello [HH]")
    assert not any("entry:essence" in _ids(m) for m in channel.sent)
    assert "Welcome from Helix & Harvest" in channel.sent[-1]["text"]
    user = await _user()
    assert (user.entry_source, user.active_mode) == ("helix_harvest", "companion")
    assert await _links() == {"helix_harvest": "referred"}

    # Asking for treatment support without an Essence link asks first instead of switching
    await handle_inbound(inbound("/aftercare"))
    assert "Were you treated at Essence?" in channel.sent[-1]["text"]
    assert (await _user()).active_mode == "companion"


async def test_web_chat_src_and_ad_referrals_set_the_entry(channel):
    set_provider(FakeProvider([]))
    msg = inbound("hi")
    msg.entry = "essence"
    await handle_inbound(msg)
    assert await _links() == {"essence": "referred"}

    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "15550002222", "id": "wamid.ad1", "type": "text", "text": {"body": "Hi!"},
         "referral": {"source_url": "https://fb.me/x", "headline": "Eat well with Helix (HH)", "body": ""}},
        {"from": "15550002222", "id": "wamid.w1", "type": "request_welcome"},
    ]}}]}]}
    msgs, _ = parse_webhook(payload)
    assert "(HH)" in msgs[0].referral and msgs[1].type == "welcome"


async def test_a_real_question_before_consent_is_answered_instead_of_the_triage(channel):
    set_provider(FakeProvider([]))
    await handle_inbound(inbound("I had botox yesterday and my forehead feels tight"))
    set_provider(FakeProvider([reply("Thanks for telling me…")]))
    await handle_inbound(inbound("I Agree", button_id="consent:yes"))
    assert channel.sent[-1]["text"] == "Thanks for telling me…"
    assert not any("entry:essence" in _ids(m) for m in channel.sent)


async def test_ice_breaker_tapped_before_consent_routes_after_consent(channel):
    set_provider(FakeProvider([]))
    await handle_inbound(inbound("Help me with my health goals"))
    await handle_inbound(inbound("I Agree", button_id="consent:yes"))
    assert "Your health companion" in channel.sent[-2]["text"] and channel.sent[-1]["type"] == "list"


# ---------- menu and modes ----------


async def test_menu_rows_depend_on_the_user_and_fit_whatsapp_limits(channel):
    await consent(channel)
    await handle_inbound(inbound("MENU"))
    menu = channel.sent[-1]
    assert menu["type"] == "list" and menu["button"] == "Open menu"
    assert "entry:essence" in _ids(menu) and "mode:post_treatment" not in _ids(menu)
    assert all(len(r["title"]) <= 24 and len(r["description"]) <= 72 for r in menu["rows"])

    await handle_inbound(inbound("I'm an Essence client", button_id="entry:essence"))
    assert await _links() == {"essence": "self_reported"}
    await handle_inbound(inbound("/menu"))
    assert "mode:post_treatment" in _ids(channel.sent[-1]) and "entry:essence" not in _ids(channel.sent[-1])


async def test_menu_before_consent_shows_onboarding_not_the_menu(channel):
    set_provider(FakeProvider([]))
    await handle_inbound(inbound("MENU"))
    assert channel.sent[-1]["type"] == "buttons" and _ids(channel.sent[-1]) == ["consent:yes", "consent:no"]
    await handle_inbound(inbound("menu"))
    assert _ids(channel.sent[-1]) == ["consent:yes", "consent:no"]


async def test_switching_modes_keeps_assessment_progress(channel):
    await consent(channel, first="Hi (ESS)")
    user = await _user()
    async with session_scope() as db:
        db.add(CompanionAssessment(user_id=user.id, goal="lose_weight", current_slot="exercise",
                                   asked_slots=["body_measures", "age"], skipped_slots=["sex"]))
    await handle_inbound(inbound("My health goals", button_id="mode:companion"))
    assert "Last time we were working on *Lose weight*" in channel.sent[-1]["text"]
    await handle_inbound(inbound("/aftercare"))
    assert (await _user()).active_mode == "post_treatment"
    await handle_inbound(inbound("MENU"))
    assert channel.sent[-1]["rows"][0]["title"] == "Continue my assessment"
    await handle_inbound(inbound("Continue my assessment", button_id="mode:companion"))
    async with session_scope() as db:
        a = await db.scalar(select(CompanionAssessment))
        assert (a.current_slot, a.asked_slots, a.skipped_slots) == ("exercise", ["body_measures", "age"], ["sex"])
    assert (await _user()).active_mode == "companion"


async def test_plan_privacy_and_person_from_the_menu(channel):
    await consent(channel)
    await handle_inbound(inbound("My plan", button_id="menu:plan"))
    assert "don't have a plan yet" in channel.sent[-1]["text"]
    await handle_inbound(inbound("Privacy & settings", button_id="menu:privacy"))
    assert "DELETE MY DATA" in channel.sent[-1]["text"] and "marketing" in channel.sent[-1]["text"]
    await handle_inbound(inbound("Talk to a person", button_id="menu:human"))
    assert "Aster care team" in channel.sent[-1]["text"]
    async with session_scope() as db:
        assert (await db.scalar(select(Handoff))).team == "aster"


async def test_human_in_treatment_mode_goes_to_essence(channel):
    await consent(channel, first="Hi (ESS)")
    await handle_inbound(inbound("My treatment", button_id="mode:post_treatment"))
    await handle_inbound(inbound("HUMAN"))
    assert "Essence team" in channel.sent[-1]["text"]
    async with session_scope() as db:
        assert (await db.scalar(select(Handoff))).team == "essence"


# ---------- returning users ----------


async def _age_last_message(hours: int) -> None:
    async with session_scope() as db:
        user = await db.scalar(select(User))
        user.last_inbound_at = utcnow() - timedelta(hours=hours)


async def test_returning_user_saying_hi_is_offered_to_continue(channel):
    await consent(channel)
    user = await _user()
    async with session_scope() as db:
        db.add(CompanionAssessment(user_id=user.id, goal="gut_health", status="paused"))
    await _age_last_message(30)
    await handle_inbound(inbound("hi"))
    assert "Welcome back, Emily" in channel.sent[-1]["text"] and "*Better digestion*" in channel.sent[-1]["text"]
    assert _ids(channel.sent[-1]) == ["mode:companion", "menu:open"]
    assert (await _user()).returned_at is None  # only the first message after the gap counts


async def test_returning_user_with_a_real_message_gets_an_answer_not_a_menu(channel):
    await consent(channel)
    await _age_last_message(30)
    set_provider(FakeProvider([reply("Sure – tell me more.")]))
    await handle_inbound(inbound("Can I ask about protein?"))
    assert channel.sent[-1]["text"] == "Sure – tell me more."


async def test_short_gap_hi_goes_to_the_agent(channel):
    await consent(channel)
    await _age_last_message(2)
    set_provider(FakeProvider([reply("Hi again!")]))
    await handle_inbound(inbound("hi"))
    assert channel.sent[-1]["text"] == "Hi again!"


async def test_users_from_before_the_companion_keep_treatment_support():
    from datetime import date

    from app import router
    from app.models import Journey

    async with session_scope() as db:
        old = User(wa_id="15550009999", onboarding_state="active", entry_source=None)
        new = User(wa_id="15550008888", onboarding_state="active")
        db.add_all([old, new])
        await db.flush()
        db.add(Journey(user_id=old.id, treatment_slug="neuromodulator", treatment_label_raw="Botox",
                       treatment_date=date(2026, 10, 1)))
        await db.flush()
        assert await router.backfill_essence_links(db) == 1
        assert await router.backfill_essence_links(db) == 0  # idempotent
        assert old.entry_source == "essence"
    assert await _links() == {"essence": "verified"}
