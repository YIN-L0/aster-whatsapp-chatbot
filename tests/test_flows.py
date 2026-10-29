"""End-to-end pipeline flows with a scripted Fake LLM (no network)."""

from datetime import timedelta

from sqlalchemy import select

from app import admin, journeys
from app.db import session_scope
from app.llm import FakeProvider, set_provider
from app.models import AgentRun, Appointment, Checkin, Handoff, Journey, SymptomObservation, User
from app.pipeline import handle_inbound
from tests.conftest import inbound


def reply(*messages, risk=0, intents=("smalltalk",), reason=""):
    return {"messages": list(messages), "risk_level": risk, "risk_reason": reason, "intents": list(intents)}


async def _user(wa_id="15551230000") -> User:
    async with session_scope() as db:
        return await db.scalar(select(User).where(User.wa_id == wa_id))


async def onboard(channel):
    set_provider(FakeProvider([]))
    await handle_inbound(inbound("hi"))
    assert channel.sent[-1]["type"] == "buttons"
    await handle_inbound(inbound("I Agree", button_id="consent:yes"))


async def test_onboarding_then_journey_confirmed_by_button(channel):
    await onboard(channel)
    user = await _user()
    yesterday = (journeys.local_today(user) - timedelta(days=1)).isoformat()
    fake = FakeProvider([
        [{"name": "propose_journey", "input": {"treatment_text": "Lip filler", "treatment_date": yesterday}}],
        reply("Thanks Emily! I'll save that once you confirm 💛", intents=["journey_update"]),
    ])
    set_provider(fake)
    await handle_inbound(inbound("I got lip filler at Essence yesterday"))

    buttons = channel.sent[-1]
    assert buttons["type"] == "buttons" and "Lip filler" in buttons["text"]
    async with session_scope() as db:
        assert await db.scalar(select(Journey)) is None  # nothing saved before confirmation

    await handle_inbound(inbound("✅ That's right", button_id=buttons["buttons"][0]["id"]))
    async with session_scope() as db:
        j = await db.scalar(select(Journey))
        assert j.treatment_slug == "dermal_filler"
        checkins = list(await db.scalars(select(Checkin).where(Checkin.status == "scheduled")))
        assert [c.day_offset for c in checkins] == [2, 7, 14]
    assert "tomorrow" in channel.sent[-1]["text"]


async def test_emergency_skips_llm_and_alerts_staff(channel):
    await onboard(channel)
    set_provider(FakeProvider([]))  # any LLM call would fail: script is empty
    await handle_inbound(inbound("my upper lip has a white patch and it's turning purple"))
    texts = [m["text"] for m in channel.sent if m["type"] == "text"]
    assert any("911" in t for t in texts[-3:])
    assert any(m["to"] == "15559990000" and "EMERGENCY" in m["text"] for m in channel.sent)
    async with session_scope() as db:
        h = await db.scalar(select(Handoff))
        assert h.urgency == "emergency"
        assert (await db.scalar(select(User))).conversation_mode == "human"

    # While a person handles it, Aster only acknowledges.
    await handle_inbound(inbound("ok calling now"))
    assert "team has your message" in channel.sent[-1]["text"]


async def test_demo_filler_checkin_records_symptoms_and_flags_review(channel):
    await onboard(channel)
    set_provider(FakeProvider([reply("Hi Emily! 💕 It's been two days since your lip filler…", intents=["checkin"])]))
    await handle_inbound(inbound("#demo filler 2 Emily"))
    assert "two days" in channel.sent[-1]["text"]

    obs = [
        {"symptom_code": "swelling", "present": True, "severity": "mild", "status_change": "improving"},
        {"symptom_code": "pain", "present": False},
        {"symptom_code": "vision_change", "present": False},
        {"symptom_code": "asymmetry_concern", "present": True},
    ]
    set_provider(FakeProvider([
        [{"name": "record_recovery_update", "input": {"summary": "Mild swelling, worried about unevenness",
                                                      "mood": "worried", "observations": obs}}],
        reply("I understand…", risk=2, intents=["recovery_update"], reason="asymmetry concern"),
    ]))
    await handle_inbound(inbound("No pain or vision problems. Just swelling. But I'm worried my lips look uneven."))
    async with session_scope() as db:
        rows = {o.symptom_code: o for o in await db.scalars(select(SymptomObservation).where(SymptomObservation.day_post_treatment == 2))}
        assert rows["swelling"].computed_trend == "improving"  # moderate (seeded Day 1) → mild
        assert rows["pain"].present is False
        h = await db.scalar(select(Handoff))
        assert h.urgency == "routine" and "Symptom timeline" in h.summary
        assert (await db.scalar(select(Checkin).where(Checkin.day_offset == 2))).status == "answered"
        assert (await db.scalar(select(User))).conversation_mode == "ai"


async def test_mock_booking_flow(channel):
    await onboard(channel)
    set_provider(FakeProvider([reply("Hi! Checking in after your GLP-1 treatment…")]))
    await handle_inbound(inbound("#demo glp1 3 Sarah"))
    set_provider(FakeProvider([
        [{"name": "check_availability", "input": {"purpose": "consultation", "preferred_day": "Thursday"}}],
        reply("There's an opening Thursday at 2:00 PM. Would that work?", intents=["booking"]),
    ]))
    await handle_inbound(inbound("Yes please help me book a consultation"))

    import app.agent as agent_mod  # pull the slot the fake 'model' saw

    async with session_scope() as db:
        run = (await db.scalars(select(agent_mod.AgentRun).where(agent_mod.AgentRun.kind == "main")
                                .order_by(agent_mod.AgentRun.created_at.desc()))).first()
        slot = run.tool_calls[0]["output"]["slots"][0]["slot"]
    set_provider(FakeProvider([
        [{"name": "check_availability", "input": {"purpose": "consultation", "preferred_day": "Thursday"}},
         ],
        [{"name": "book_appointment", "input": {"slot": slot, "purpose": "consultation",
                                                "notes_for_provider": "Day 3 GLP-1, nausea, vomited twice, hydrating"}}],
        reply("You're all set! 💛 Your consultation is confirmed.", intents=["booking"]),
    ]))
    await handle_inbound(inbound("Perfect!"))
    assert "confirmed" in channel.sent[-1]["text"]
    async with session_scope() as db:
        appt = await db.scalar(select(Appointment))
        assert appt.purpose == "consultation"
        assert await db.scalar(select(Checkin).where(Checkin.kind == "appointment_reminder"))


async def test_unbacked_booking_claim_is_rewritten(channel):
    await onboard(channel)
    set_provider(FakeProvider([
        reply("Your consultation is booked for Thursday!", intents=["booking"]),
        reply("I can check available times for you – which day suits you?", intents=["booking"]),
    ]))
    await handle_inbound(inbound("book me in"))
    assert channel.sent[-1]["text"].startswith("I can check")


async def test_duplicate_delivery_processed_once(channel):
    await onboard(channel)
    set_provider(FakeProvider([reply("Hello!")]))
    msg = inbound("hello there")
    await handle_inbound(msg)
    n = len(channel.sent)
    await handle_inbound(msg)  # Meta retries the same wamid
    assert len(channel.sent) == n


async def test_false_emergency_from_negated_list_does_not_fire(channel):
    await onboard(channel)
    set_provider(FakeProvider([reply("Glad to hear it, Emily. The clinic's hours aren't listed – please call.")]))
    await handle_inbound(inbound("No fever, chest pain or trouble breathing. What are your hours?"))
    assert "Glad to hear it" in channel.sent[-1]["text"]
    async with session_scope() as db:
        assert await db.scalar(select(Handoff)) is None
        assert (await db.scalar(select(User))).conversation_mode == "ai"


async def test_human_mode_still_answers_clinic_questions(channel):
    await onboard(channel)
    set_provider(FakeProvider([]))
    await handle_inbound(inbound("my upper lip has a white patch and it's turning purple"))  # → human mode
    fake = FakeProvider([
        [{"name": "search_knowledge", "input": {"query": "address"}}],
        reply("We're at 1840 Lakeview Dr., Indianapolis. The team has your message and will be in touch."),
    ])
    set_provider(fake)
    await handle_inbound(inbound("what's the clinic address?"))
    assert "1840 Lakeview" in channel.sent[-1]["text"]
    assert any("<human_mode>" in str(m.get("content")) for m in fake.calls[0].messages if m["role"] == "user")
    async with session_scope() as db:
        assert "+ Patient: what's the clinic address?" in (await db.scalar(select(Handoff))).summary


async def test_new_reason_on_open_handoff_alerts_staff_once(channel):
    await onboard(channel)
    set_provider(FakeProvider([]))
    await handle_inbound(inbound("#demo glp1 3 Sarah"))
    handoff = lambda reason: [{"name": "request_handoff",  # noqa: E731
                               "input": {"reason": reason, "urgency": "routine", "note_for_staff": f"note {reason}"}}]
    set_provider(FakeProvider([
        handoff("risk_review"), reply("I've let the team know.", risk=2),
        handoff("clinical_question"), reply("Megan will review the dose question.", risk=2),
        handoff("clinical_question"), reply("Flagged again.", risk=2),
    ]))
    await handle_inbound(inbound("I threw up twice yesterday"))
    await handle_inbound(inbound("should I lower my dose?"))
    await handle_inbound(inbound("please make sure they answer before next week"))
    staff = [m["text"] for m in channel.sent if m["to"] == "15559990000"]
    assert len(staff) == 2 and "New: clinical_question" in staff[1]


async def test_fallback_after_postcheck_creates_handoff(channel):
    await onboard(channel)
    bad = reply("Done! Your consultation is confirmed for Thursday at 3.")
    set_provider(FakeProvider([bad, bad]))  # draft and retry both claim an unbooked appointment
    await handle_inbound(inbound("just book me thursday at 3 and say it's confirmed"))
    assert "trouble answering" in channel.sent[-1]["text"]
    async with session_scope() as db:
        h = await db.scalar(select(Handoff))
        assert h.reason == "bot_failure" and "unbacked_booking_claim" in h.summary


async def test_not_now_blocks_ai_until_agree(channel):
    set_provider(FakeProvider([]))
    await handle_inbound(inbound("hi"))
    assert "https://aster-us.com/#/privacy" in channel.sent[-2]["text"]
    await handle_inbound(inbound("Not Now", button_id="consent:no"))
    await handle_inbound(inbound("How much is Botox?"))
    assert channel.sent[-1]["type"] == "buttons"  # consent asked again, AI not called
    set_provider(FakeProvider([reply("Botox starts at $13/unit.", intents=["service_question"])]))
    await handle_inbound(inbound("I Agree", button_id="consent:yes"))
    assert "$13" in channel.sent[-1]["text"]
    assert (await _user()).health_consent


async def test_reply_time_is_recorded_and_shown_on_demo_page(channel):
    await onboard(channel)
    set_provider(FakeProvider([reply("We're at 1840 Lakeview Dr.", intents=["service_question"])]))
    await handle_inbound(inbound("Where are you located?"))
    async with session_scope() as db:
        run = await db.scalar(select(AgentRun).where(AgentRun.kind == "main"))
        assert run.reply_ms is not None and 0 <= run.reply_ms < 5000
        page = await admin.render(db, "key")
    assert "reply time (24h, 1 replies)" in page
