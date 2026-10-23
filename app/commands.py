"""Deterministic commands. Patient commands: STOP, START, HUMAN, DELETE MY DATA.
Demo commands (DEMO_MODE only): #demo, #checkin, #reminder, #status, #resolve, #reset, #help.
"""

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import booking, journeys, outbound
from app.config import get_settings
from app.followups import plan_for_journey, send_checkin
from app.handoff import create_handoff, resolve_all
from app.models import (
    AgentRun, Appointment, Checkin, Handoff, JournalEntry, Journey, Message, PendingAction,
    SymptomObservation, User, utcnow,
)

DEMO_SCENARIOS = {
    # key: (protocol slug, label, default day, provider)
    "glp1": ("glp1", "GLP-1 (semaglutide) injection", 3, "Megan Clarke, FNP-BC"),
    "nad": ("nad_plus", "NAD+ injection", 1, "Melissa Rowe, PA-C"),
    "filler": ("dermal_filler", "Lip filler", 2, "Claire Benson, RN"),
    "body": ("body_contouring", "truSculpt iD body contouring", 1, "Diane Mercer, FNP-BC"),
    "botox": ("neuromodulator", "Botox", 1, "Claire Benson, RN"),
}

HELP = """*Aster demo commands*
#demo glp1 | nad | filler | body | botox [day] [name] – start a scenario and send that day's check-in
   e.g. "#demo filler 2 Emily"
#checkin – jump forward to the next scheduled check-in and send it now
#reminder – send the next appointment reminder now
#status – what Aster has recorded for you
#resolve – close open handoffs (staff action) and return to AI mode
#reset – delete all your demo data and start from onboarding
Patient commands: STOP, START, HUMAN, DELETE MY DATA"""


async def wipe_user_data(db: AsyncSession, user: User, keep_messages: bool = False) -> None:
    jids = list(await db.scalars(select(Journey.id).where(Journey.user_id == user.id)))
    if jids:
        await db.execute(delete(SymptomObservation).where(SymptomObservation.journey_id.in_(jids)))
    for model in (JournalEntry, Checkin, Appointment, Handoff, PendingAction, AgentRun, Journey):
        await db.execute(delete(model).where(model.user_id == user.id))
    if not keep_messages:
        await db.execute(delete(Message).where(Message.user_id == user.id))


async def handle(db: AsyncSession, user: User, text: str) -> bool:
    t = text.strip()
    upper = t.upper()
    s = get_settings()

    if upper in ("STOP", "UNSUBSCRIBE"):
        user.proactive_opt_in = False
        await db.execute(
            Checkin.__table__.update()
            .where(Checkin.user_id == user.id, Checkin.status == "scheduled")
            .values(status="cancelled")
        )
        await outbound.send_text(db, user, "Got it – I've paused check-in messages. You can still message me anytime, "
                                           "and reply START to turn check-ins back on.", sender="aster_system")
        return True
    if upper == "START":
        user.proactive_opt_in = True
        journey = await journeys.get_primary_journey(db, user.id)
        if journey:
            await plan_for_journey(db, user, journey)
        await outbound.send_text(db, user, "Check-ins are back on 💛", sender="aster_system")
        return True
    if upper in ("HUMAN", "AGENT", "TALK TO A PERSON"):
        await create_handoff(db, user, reason="user_request", urgency="routine", note="Patient asked for a person.")
        await outbound.send_text(db, user, "Of course – I've asked the Essence team to reach out to you. You can also "
                                           "call the clinic at (317) 555-0142.", sender="aster_system")
        return True
    if upper == "DELETE MY DATA":
        await create_handoff(db, user, reason="deletion_request", urgency="routine", note="Patient requested data deletion.")
        await outbound.send_text(db, user, "I've sent your deletion request to the team. They'll confirm once your "
                                           "data has been removed.", sender="aster_system")
        return True

    if not (s.demo_mode and t.startswith("#")):
        return False

    parts = t.split()
    cmd = parts[0].lower()

    if cmd == "#help":
        await outbound.send_text(db, user, HELP, sender="aster_system")
        return True

    if cmd == "#reset":
        await wipe_user_data(db, user)
        user.onboarding_state, user.health_consent, user.conversation_mode = "new", False, "ai"
        await outbound.send_text(db, user, "🧹 Demo data deleted. Send any message to start again.", sender="aster_system")
        return True

    if cmd == "#resolve":
        n = await resolve_all(db, user)
        await outbound.send_text(db, user, f"✅ {n} handoff(s) resolved – Aster is back in AI mode.", sender="aster_system")
        return True

    if cmd == "#status":
        await outbound.send_text(db, user, await status_text(db, user), sender="aster_system")
        return True

    if cmd == "#demo":
        key = parts[1].lower() if len(parts) > 1 else ""
        if key not in DEMO_SCENARIOS:
            await outbound.send_text(db, user, HELP, sender="aster_system")
            return True
        slug, label, default_day, provider = DEMO_SCENARIOS[key]
        day = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else default_day
        name = parts[3] if len(parts) > 3 else (parts[2] if len(parts) > 2 and not parts[2].isdigit() else None)
        await wipe_user_data(db, user)
        if name:
            user.first_name = name.capitalize()
        user.onboarding_state, user.health_consent, user.proactive_opt_in, user.conversation_mode = "active", True, True, "ai"
        today = journeys.local_today(user)
        journey = await journeys.commit_journey(
            db, user,
            {"treatment_slug": slug, "treatment_text": label, "treatment_date": (today - timedelta(days=day)).isoformat(),
             "provider_name": provider},
            source="demo_seed",
        )
        if key == "filler" and day >= 2:
            # Seed a Day-1 journal entry so the demo can show symptom memory and trends.
            entry = JournalEntry(user_id=user.id, journey_id=journey.id, day_post_treatment=1,
                                 summary="Moderate lip swelling and mild bruising, no pain.", extracted_by="ai")
            db.add(entry)
            await db.flush()
            for code, present, sev in (("swelling", True, "moderate"), ("bruising", True, "mild"), ("pain", False, "none")):
                db.add(SymptomObservation(journal_entry_id=entry.id, journey_id=journey.id, symptom_code=code,
                                          present=present, severity=sev, computed_trend="new" if present else None,
                                          day_post_treatment=1))
        await plan_for_journey(db, user, journey)
        checkin = Checkin(user_id=user.id, journey_id=journey.id, kind="recovery", day_offset=day, due_at=utcnow())
        db.add(checkin)
        await db.flush()
        await send_checkin(db, user, checkin)
        return True

    if cmd == "#checkin":
        nxt = await db.scalar(
            select(Checkin).where(Checkin.user_id == user.id, Checkin.status == "scheduled", Checkin.kind == "recovery")
            .order_by(Checkin.due_at)
        )
        journey = await db.get(Journey, nxt.journey_id) if nxt else None
        if not nxt or not journey:
            await outbound.send_text(db, user, "No more check-ins scheduled for this journey.", sender="aster_system")
            return True
        # Time travel: move the treatment date back so that today is the check-in day.
        journey.treatment_date = journeys.local_today(user) - timedelta(days=nxt.day_offset)
        nxt.due_at = utcnow()
        await send_checkin(db, user, nxt)
        await plan_for_journey(db, user, journey)
        return True

    if cmd == "#reminder":
        nxt = await db.scalar(
            select(Checkin).where(Checkin.user_id == user.id, Checkin.status == "scheduled",
                                  Checkin.kind == "appointment_reminder").order_by(Checkin.due_at)
        )
        if not nxt:
            await outbound.send_text(db, user, "No appointment reminders scheduled.", sender="aster_system")
            return True
        await send_checkin(db, user, nxt)
        return True

    await outbound.send_text(db, user, HELP, sender="aster_system")
    return True


async def status_text(db: AsyncSession, user: User) -> str:
    lines = [f"*What Aster has recorded for {user.first_name or 'you'}*"]
    journey = await journeys.get_primary_journey(db, user.id)
    if journey:
        lines.append(f"Journey: {journey.treatment_label_raw}, {journey.treatment_date:%b %d} "
                     f"(Day {journeys.day_post_treatment(user, journey)})")
        lines.append("Symptoms:\n" + await journeys.symptom_timeline(db, journey.id))
    else:
        lines.append("No treatment journey yet.")
    tz = journeys.ZoneInfo(user.timezone)
    for a in await booking.upcoming_appointments(db, user.id):
        lines.append(f"Appointment: {a.purpose} – {booking.fmt_slot(a.starts_at.astimezone(tz))} (demo booking)")
    nxt = await db.scalar(select(Checkin).where(Checkin.user_id == user.id, Checkin.status == "scheduled")
                          .order_by(Checkin.due_at))
    if nxt:
        lines.append(f"Next message: {nxt.kind.replace('_', ' ')} on {nxt.due_at.astimezone(tz):%a %b %d %H:%M}")
    handoffs = list(await db.scalars(select(Handoff).where(Handoff.user_id == user.id, Handoff.status == "open")))
    for h in handoffs:
        lines.append(f"Open handoff: {h.urgency} ({h.reason})")
    return "\n".join(lines)
