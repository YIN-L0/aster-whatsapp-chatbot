"""Companion assessment and plans in the chat (docs/COMPANION_REDESIGN.md §6.3, phase C4)."""

from sqlalchemy import func, select

from app.context import build_context_block
from app.db import session_scope
from app.llm import FakeProvider, set_provider
from app.models import AgentRun, CompanionAssessment, Handoff, Plan, ProfileFact, User, UserGoal
from app.pipeline import handle_inbound
from tests.conftest import inbound
from tests.test_flows import reply

PLAN = {
    "situation_summary": "You walk a little and often have a soda with lunch.",
    "priorities": ["Swap the lunchtime soda for sparkling water", "Add two brisk walks a week"],
    "daily_habits": ["Water with every meal"],
    "weekly_habits": ["Plan two home-cooked dinners"],
    "metrics": ["Weight once a week, same day and time"],
    "review_in_days": 14,
}


def call(name, **args):
    return [{"name": name, "input": args}]


async def _user() -> User:
    async with session_scope() as db:
        return await db.scalar(select(User))


async def _outputs() -> list[dict]:
    async with session_scope() as db:
        run = (await db.scalars(select(AgentRun).where(AgentRun.kind == "main")
                                .order_by(AgentRun.created_at.desc()))).first()
        return [c["output"] for c in run.tool_calls]


async def _assessment() -> CompanionAssessment:
    async with session_scope() as db:
        return await db.scalar(select(CompanionAssessment).order_by(CompanionAssessment.created_at.desc()))


async def companion_user(channel):
    set_provider(FakeProvider([]))
    await handle_inbound(inbound("hi"))
    await handle_inbound(inbound("I Agree", button_id="consent:yes"))
    await handle_inbound(inbound("No, my health goals", button_id="entry:direct"))


async def start_lose_weight(channel):
    await companion_user(channel)
    fake = FakeProvider([call("set_goal", goal="lose_weight"), reply("Happy to help. First: height and weight?")])
    set_provider(fake)
    await handle_inbound(inbound("Lose weight", button_id="goal:lose_weight"))
    return fake


async def test_goal_from_the_list_starts_the_assessment_with_an_intro_and_a_purposeful_question(channel):
    fake = await start_lose_weight(channel)
    said = [m["content"] for m in fake.calls[0].messages if m["role"] == "user"][-1]
    assert "My goal: Lose weight [goal=lose_weight]" in said
    out = (await _outputs())[0]
    assert out["status"] == "started" and "intro" in out
    assert out["next_question"]["slot"] == "body_measures" and out["next_question"]["examples"]
    a = await _assessment()
    assert (a.goal, a.status, a.current_slot, a.intro_shown) == ("lose_weight", "in_progress", "body_measures", True)
    async with session_scope() as db:
        assert (await db.scalar(select(UserGoal))).is_focus


async def test_answers_are_saved_converted_and_merged_and_the_next_question_follows(channel):
    await start_lose_weight(channel)
    set_provider(FakeProvider([call("record_profile_facts", facts={"body_measures": {"weight_lb": 180}}),
                               reply("Got it – and your height?")]))
    await handle_inbound(inbound("I'm 180 lbs"))
    out = (await _outputs())[0]
    assert out["saved"] == ["body_measures"] and "height_cm" in out["problems"]["body_measures"][0]
    assert out["next_question"]["slot"] == "body_measures"  # still incomplete – ask for the missing part

    set_provider(FakeProvider([call("record_profile_facts",
                                    facts={"body_measures": {"height_ft": 5, "height_in": 9}, "age": {"age": 41}}),
                               reply("Thanks! How active are you in a typical week?")]))
    await handle_inbound(inbound("5'9\", and I'm 41"))
    out = (await _outputs())[0]
    assert out["next_question"]["slot"] == "exercise" and any("BMI" in f for f in out["findings_so_far"])
    async with session_scope() as db:
        current = await db.scalar(select(ProfileFact).where(ProfileFact.key == "body_measures",
                                                            ProfileFact.superseded_at.is_(None)))
        assert current.value == {"weight_kg": 81.6, "height_cm": 175.3}
        assert await db.scalar(select(func.count()).select_from(ProfileFact)) == 3  # the older row is kept as history


async def test_skip_and_pause_keep_progress(channel):
    await start_lose_weight(channel)
    set_provider(FakeProvider([call("skip_question", slot="body_measures"), reply("No problem. How old are you?")]))
    await handle_inbound(inbound("I'd rather not say my weight"))
    assert (await _outputs())[0]["next_question"]["slot"] == "age"
    set_provider(FakeProvider([call("pause_assessment"), reply("Sure – pick up anytime.")]))
    await handle_inbound(inbound("let's do this later"))
    a = await _assessment()
    assert (a.status, a.skipped_slots) == ("paused", ["body_measures"])


async def test_context_carries_the_next_question_profile_and_food_estimates(channel):
    await start_lose_weight(channel)
    set_provider(FakeProvider([call("record_profile_facts", facts={
        "body_measures": {"weight_kg": 80, "height_cm": 170}, "age": {"age": 40},
        "exercise": {"activities": []}}), reply("Thanks – what does a typical day of eating look like?")]))
    await handle_inbound(inbound("80 kg, 170 cm, 40, and I don't really exercise"))
    async with session_scope() as db:
        user = await db.scalar(select(User))
        ctx = await build_context_block(db, user, None, None, [])
    assert "NEXT QUESTION: slot=meals" in ctx and "FOOD ESTIMATES" in ctx
    assert "readiness=preliminary" in ctx and "PROFILE" in ctx and "body_measures" in ctx
    assert "PROTOCOL" not in ctx


async def test_plan_needs_enough_information_unless_the_user_asks(channel):
    await start_lose_weight(channel)
    set_provider(FakeProvider([call("generate_plan", plan=PLAN, user_asked=False), reply("Let's ask a bit more first.")]))
    await handle_inbound(inbound("ok"))
    assert (await _outputs())[0]["error"] == "not_enough_information"

    set_provider(FakeProvider([
        call("get_plan_inputs"),
        call("generate_plan", plan=PLAN, user_asked=True),
        reply("Here's a first version – want to change anything?"),
    ]))
    await handle_inbound(inbound("Just give me a plan now please"))
    inputs, saved = await _outputs()
    assert inputs["confidence"] == "preliminary" and "Not known yet: height and weight" in inputs["limitations"]
    assert saved["status"] == "saved" and saved["version"] == 1
    texts = [m["text"] for m in channel.sent if m["type"] == "text"]
    assert texts[-2] == "Here's a first version – want to change anything?"
    assert "📋 *Your plan – Lose weight* (version 1" in texts[-1] and "first version" in texts[-1]
    assert "Swap the lunchtime soda" in texts[-1]


async def test_plans_never_sell_and_updates_create_versions(channel):
    await start_lose_weight(channel)
    selling = {**PLAN, "weekly_habits": ["Book the Essence weight program"]}
    set_provider(FakeProvider([
        call("generate_plan", plan=selling, user_asked=True),
        call("generate_plan", plan=PLAN, user_asked=True),
        reply("Here's your first plan."),
    ]))
    await handle_inbound(inbound("plan please"))
    rejected, saved = await _outputs()
    assert rejected["error"] == "plan_rejected" and "providers or products" in rejected["problems"][0]
    assert saved["version"] == 1

    set_provider(FakeProvider([call("generate_plan", plan={**PLAN, "priorities": ["Walk 20 minutes after dinner"]},
                                    user_asked=True), reply("Updated!")]))
    await handle_inbound(inbound("I can't do brisk walks, can we change that?"))
    async with session_scope() as db:
        plans = list(await db.scalars(select(Plan).order_by(Plan.version)))
    assert [(p.version, p.status) for p in plans] == [(1, "superseded"), (2, "active")]
    await handle_inbound(inbound("My plan", button_id="menu:plan"))
    assert "version 2" in channel.sent[-1]["text"] and "Walk 20 minutes" in channel.sent[-1]["text"]


async def test_gi_red_flag_raises_an_urgent_handoff_in_code(channel):
    await companion_user(channel)
    set_provider(FakeProvider([
        call("set_goal", goal="gut_health"),
        call("record_profile_facts", facts={"gut_symptoms": {"gi_symptoms": ["blood_in_stool"]}}),
        reply("Thanks for telling me. This needs a doctor soon."),  # the model said risk 0 – code still escalates
    ]))
    await handle_inbound(inbound("I want better digestion, I've seen blood in my stool this week"))
    out = (await _outputs())[1]
    assert out["safety"]["handoff"] == "urgent" and out["readiness"] == "blocked"
    async with session_scope() as db:
        h = await db.scalar(select(Handoff))
        assert (h.reason, h.urgency, h.team) == ("gi_red_flag", "urgent", "aster")
        run = await db.scalar(select(AgentRun).where(AgentRun.kind == "main").order_by(AgentRun.created_at.desc()))
        assert run.risk_final == 3
    assert (await _user()).conversation_mode == "human"


async def test_a_teenager_gets_a_routine_handoff_and_a_plan_without_calories(channel):
    await start_lose_weight(channel)
    set_provider(FakeProvider([
        call("record_profile_facts", facts={"body_measures": {"weight_kg": 70, "height_cm": 165}, "age": {"age": 16}}),
        call("get_plan_inputs"),
        reply("Thanks – I'll keep things general and ask our team to reach out."),
    ]))
    await handle_inbound(inbound("70 kg, 165 cm, I'm 16"))
    recorded, inputs = await _outputs()
    assert recorded["safety"]["handoff"] == "routine"
    assert inputs["energy_need_kcal"] is None and inputs["weight_loss_target_kcal"] is None and inputs["cautions"]
    async with session_scope() as db:
        assert (await db.scalar(select(Handoff))).reason == "special_population"


async def test_chat_only_goals_are_remembered_without_an_assessment(channel):
    await companion_user(channel)
    set_provider(FakeProvider([call("set_goal", goal="sleep", in_their_words="I wake up at 4am"),
                               reply("Waking at 4am is rough…")]))
    await handle_inbound(inbound("I keep waking up at 4am"))
    assert (await _outputs())[0]["structured"] is False
    async with session_scope() as db:
        assert await db.scalar(select(func.count()).select_from(CompanionAssessment)) == 0
        user = await db.scalar(select(User))
        ctx = await build_context_block(db, user, None, None, [])
    assert "sleep (focus)" in ctx and "chat only" in ctx


async def test_a_second_goal_reuses_known_answers(channel):
    await start_lose_weight(channel)
    set_provider(FakeProvider([call("record_profile_facts", facts={
        "body_measures": {"weight_kg": 80, "height_cm": 170}, "exercise": {"activities": []},
        "meals": {"meals_per_day": 3, "diet_flags": ["sugary_drinks"]}}), reply("Thanks!")]))
    await handle_inbound(inbound("80kg 170cm, no exercise, 3 meals and a soda a day"))
    set_provider(FakeProvider([call("set_goal", goal="gut_health"), reply("Let's look at digestion.")]))
    await handle_inbound(inbound("Actually my main thing is bloating"))
    out = (await _outputs())[0]
    assert out["next_question"]["slot"] == "gut_symptoms"  # meals are already known
    async with session_scope() as db:
        statuses = [a.status for a in await db.scalars(select(CompanionAssessment).order_by(CompanionAssessment.created_at))]
    assert statuses == ["paused", "in_progress"]


async def test_companion_tools_need_health_consent_and_the_companion_mode(channel):
    await companion_user(channel)
    async with session_scope() as db:
        (await db.scalar(select(User))).health_consent = False
    set_provider(FakeProvider([call("set_goal", goal="lose_weight"), reply("ok")]))
    await handle_inbound(inbound("lose weight"))
    assert (await _outputs())[0]["error"] == "no_health_consent"

    async with session_scope() as db:
        user = await db.scalar(select(User))
        user.health_consent, user.active_mode = True, "post_treatment"
    set_provider(FakeProvider([call("set_goal", goal="lose_weight"), reply("ok")]))
    await handle_inbound(inbound("lose weight"))
    assert (await _outputs())[0]["error"] == "tool_not_available"


async def test_demo_console_shows_companion_data(channel):
    from app import admin

    await start_lose_weight(channel)
    set_provider(FakeProvider([call("generate_plan", plan=PLAN, user_asked=True), reply("Here you go.")]))
    await handle_inbound(inbound("plan please"))
    async with session_scope() as db:
        page = await admin.render(db, "key")
    assert "Aster companion" in page and "Entry: <b>direct</b>" in page and "v1 · active · preliminary" in page
    assert "readiness <b>not_ready</b>" in page or "readiness <b>" in page
