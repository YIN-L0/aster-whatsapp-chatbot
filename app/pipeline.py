"""Inbound message pipeline (docs/ARCHITECTURE.md §5, demo subset)."""

import asyncio
import logging
import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import booking, commands, journeys, outbound, safety
from app.agent import AgentResult, TurnState, run_agent
from app.channels import Button, InboundMessage
from app.config import get_settings
from app.context import build_context_block, build_history
from app.db import session_scope
from app.followups import active_checkin, plan_for_journey
from app.handoff import URGENCY_FOR_RISK, create_handoff, open_handoff
from app.models import AgentRun, Checkin, JournalEntry, Message, PendingAction, User, utcnow

log = logging.getLogger(__name__)
_locks: dict[uuid.UUID, asyncio.Lock] = defaultdict(asyncio.Lock)

WELCOME_TEXT = (
    "🌿 *Welcome to Aster!*\n\n"
    "Hi {name}! I'm Aster, your personal AI wellness companion.\n\n"
    "I'm here to support your wellness journey and help you stay connected with your care team.\n\n"
    "Before we get started, please review our Privacy Policy:\n{privacy_url}"
)
CONSENT_TEXT = (
    "🔒 *Your Privacy Matters*\n\n"
    "By tapping *I Agree*, you acknowledge our Privacy Policy and that:\n"
    "• Aster is an AI-powered assistant, not a healthcare professional.\n"
    "• Your messages may be processed by Aster and its authorized service providers to provide this service.\n"
    "• WhatsApp may not be appropriate for sharing sensitive medical information. Please avoid sharing it here. "
    "For medical concerns, contact your healthcare provider directly. In an emergency, call 911.\n\n"
    "Privacy Policy: {privacy_url}\n\n"
    "Reply STOP anytime to stop receiving messages."
)
CONSENT_BUTTONS = [Button("consent:yes", "I Agree"), Button("consent:no", "Not Now")]
DEMO_NOTE = "_Demo version – please don't share real health information._"


async def _send_consent(db: AsyncSession, user: User) -> None:
    await outbound.send_buttons(
        db, user, CONSENT_TEXT.format(privacy_url=get_settings().privacy_policy_url), CONSENT_BUTTONS
    )


# ---------- ingest (called from the webhook; must be fast) ----------


async def ingest(msg: InboundMessage) -> uuid.UUID | None:
    async with session_scope() as db:
        if msg.wa_message_id and await db.scalar(select(Message.id).where(Message.wa_message_id == msg.wa_message_id)):
            return None  # duplicate delivery from Meta
        user = await db.scalar(select(User).where(User.wa_id == msg.wa_id))
        if user is None:
            user = User(wa_id=msg.wa_id, display_name=msg.profile_name, timezone=get_settings().clinic_timezone)
            if msg.profile_name:
                user.first_name = msg.profile_name.split()[0][:40]
            db.add(user)
            await db.flush()
        user.last_inbound_at = utcnow()
        db.add(
            Message(
                user_id=user.id,
                direction="in",
                wa_message_id=msg.wa_message_id or None,
                type=msg.type,
                body=msg.text,
                payload={"button_id": msg.button_id} if msg.button_id else None,
                sender="user",
            )
        )
        return user.id


async def process_user(user_id: uuid.UUID) -> None:
    async with _locks[user_id]:
        try:
            async with session_scope() as db:
                await _process(db, user_id)
        except Exception:
            log.exception("processing failed user=%s", user_id)
            async with session_scope() as db:
                user = await db.get(User, user_id)
                if user:
                    await outbound.send_text(db, user, safety.FALLBACK_REPLY, sender="aster_system")


async def handle_inbound(msg: InboundMessage) -> None:
    user_id = await ingest(msg)
    if user_id:
        await process_user(user_id)


# ---------- processing ----------


async def _process(db: AsyncSession, user_id: uuid.UUID) -> None:
    user = await db.get(User, user_id)
    pending = list(
        await db.scalars(
            select(Message)
            .where(Message.user_id == user_id, Message.direction == "in", Message.status == "received")
            .order_by(Message.created_at)
        )
    )
    if not pending:
        return
    for m in pending:
        m.status = "processed"
    last = pending[-1]
    text = "\n".join(m.body for m in pending if m.body).strip()

    # A. Button replies are handled deterministically – the LLM never commits journey data.
    button_id = (last.payload or {}).get("button_id")
    if button_id:
        if button_id.startswith("slot:"):
            # A booking time button: the agent books it (it may still need the patient's name and date of birth)
            last.body = _slot_button_text(button_id, last.body)
            text = "\n".join(m.body for m in pending if m.body).strip()
        elif await _handle_button(db, user, button_id):
            return

    # B. Commands (STOP/START/HUMAN, #demo …)
    if await commands.handle(db, user, text):
        return

    if last.type == "media" and not text:
        await outbound.send_text(
            db, user,
            "Thanks for sending that! I can't view photos or voice notes yet. Could you describe in words how "
            "things look or feel? If you're worried, the Essence team can review photos directly – just ask.",
            sender="aster_system",
        )
        return

    # C. Safety rules run before anything else, including onboarding.
    rule = safety.prefilter(text)
    if rule.level >= 4:
        await outbound.send_text(
            db, user, safety.emergency_reply(user.first_name, "(317) 555-0142", rule.hits), sender="aster_system"
        )
        await create_handoff(
            db, user, reason="risk_l4", urgency="emergency",
            note=f"Emergency phrases detected: {', '.join(rule.hits)}", signals={"rules": rule.hits},
        )
        db.add(AgentRun(user_id=user.id, kind="rule_emergency", provider="rules", model="-", prompt_version="-",
                        risk_rule=4, risk_final=4, output={"hits": rule.hits}))
        return

    # D. Onboarding
    if user.onboarding_state == "new":
        user.onboarding_state = "consent_pending"
        for m in pending:
            m.status = "deferred"  # answered after consent
        s = get_settings()
        welcome = WELCOME_TEXT.format(name=user.first_name or "there", privacy_url=s.privacy_policy_url)
        if s.demo_mode:
            welcome += f"\n\n{DEMO_NOTE}"
        await outbound.send_text(db, user, welcome, sender="aster_system")
        await _send_consent(db, user)
        return
    if user.onboarding_state in ("consent_pending", "declined"):
        # No AI chat until the patient taps I Agree – typed messages wait and the consent prompt is shown again.
        for m in pending:
            m.status = "deferred"
        await _send_consent(db, user)
        return

    # E. Human mode: a person is handling the health concern. New messages are added to the handoff; Aster only
    # answers clinic questions (hours, address, prices) and otherwise says the team has the message.
    if user.conversation_mode == "human":
        handoff = await open_handoff(db, user.id)
        if handoff:
            handoff.summary += f"\n+ Patient: {text[:300]}"
        await _run_main_agent(db, user, pending, text, rule, human_mode=True)
        return

    await _run_main_agent(db, user, pending, text, rule)


HUMAN_MODE_CONTEXT = (
    "<human_mode>\nThe care team is now handling this patient's health concern. Don't advise on symptoms, ask new "
    "questions or book. Record anything they report with record_recovery_update and set risk_level honestly – if "
    "they now describe an emergency, tell them to call 911 now. Otherwise, for health topics reply in one or two "
    "sentences that the team has their message and will be in touch. You MAY answer questions about the clinic "
    "itself (hours, address, phone, prices, services) with search_knowledge or CLINIC FACTS. Don't repeat the 911 "
    "advice if you gave it in your last message.\n</human_mode>"
)


async def _run_main_agent(
    db: AsyncSession, user: User, pending: list[Message], text: str, rule, human_mode: bool = False
) -> None:
    journey = await journeys.get_primary_journey(db, user.id)
    checkin = await active_checkin(db, user)
    run = AgentRun(user_id=user.id, kind="main", provider="", model="", prompt_version="", risk_rule=rule.level)
    db.add(run)
    await db.flush()
    st = TurnState(db=db, user=user, journey=journey, checkin=checkin, agent_run=run,
                   source_message_ids=[str(m.id) for m in pending])

    history = await build_history(db, user, exclude_ids={m.id for m in pending})
    ctx = await build_context_block(db, user, journey, checkin, rule.negated_hits)
    if human_mode:
        ctx = f"{ctx}\n{HUMAN_MODE_CONTEXT}"
    final = f"{ctx}\n\n<patient_message>\n{text}\n</patient_message>"
    result = await run_agent(st, history, final, allow_tools={"search_knowledge", "record_recovery_update"} if human_mode else True)

    if result.error and human_mode:
        await outbound.send_text(db, user, safety.HUMAN_MODE_ACK, sender="aster_system")
        return
    if result.error:
        await outbound.send_text(db, user, safety.FALLBACK_REPLY, sender="aster_system")
        await create_handoff(db, user, reason="bot_failure", urgency="routine", note=f"Agent error: {result.error[:200]}")
        return

    # Post-check, one regeneration with feedback, then a conservative fallback.
    has_appt = bool(await booking.upcoming_appointments(db, user.id))
    violations = safety.postcheck("\n".join(result.messages), booked_this_turn=st.booked, has_appointment=has_appt)
    if violations:
        log.warning("postcheck violations=%s", violations)
        retry_history = history + [
            {"role": "user", "content": final},
            {"role": "assistant", "content": "\n\n".join(result.messages)},
        ]
        fix = (f"{ctx}\n\n<system_feedback>Your previous reply broke these rules: {', '.join(violations)}. "
               "Rewrite it so it follows the safety rules. Do not mention this feedback.</system_feedback>")
        retry = await run_agent(st, retry_history, fix, allow_tools=False)
        if retry.error or safety.postcheck("\n".join(retry.messages), booked_this_turn=st.booked,
                                          has_appointment=has_appt):
            result.messages = [safety.FALLBACK_REPLY]
            violations.append("fallback_used")
            # FALLBACK_REPLY says the team will follow up, so make sure someone is asked to.
            await create_handoff(db, user, reason="bot_failure", urgency="routine",
                                 note=f"Reply blocked by safety post-check ({', '.join(violations)})")
        else:
            result.messages, result.risk_level = retry.messages, max(result.risk_level, retry.risk_level)
    await _finish_turn(db, user, run, result, violations)


async def _finish_turn(db: AsyncSession, user: User, run: AgentRun, result: AgentResult, violations: list[str]) -> None:
    st = result.state
    risk = max(run.risk_rule, result.risk_level)
    for entry_id in st.recorded_entry_ids:
        if await journeys.has_worsening(db, entry_id):
            risk = max(risk, 2)
    # Any risk >= 2 must have a handoff, even if the agent forgot to request one.
    rank = {None: -1, "routine": 0, "urgent": 1, "emergency": 2}
    if risk >= 2 and rank[st.handoff_urgency] < rank[URGENCY_FOR_RISK[min(risk, 4)]]:
        await create_handoff(db, user, reason=f"risk_l{risk}", urgency=URGENCY_FOR_RISK[min(risk, 4)],
                             note=result.risk_reason or "auto-created from risk level", signals={"source": "risk_merge"})
    for entry_id in st.recorded_entry_ids:
        entry = await db.get(JournalEntry, entry_id)
        entry.risk_level = risk
    if st.checkin:
        st.checkin.status, st.checkin.answered_at = "answered", utcnow()

    run.risk_agent, run.risk_final = result.risk_level, risk
    run.output = {"messages": result.messages, "risk_reason": result.risk_reason, "intents": result.intents}
    run.postcheck_violations = violations or None

    for msg in result.messages:
        await outbound.send_text(db, user, msg, agent_run_id=run.id)
    for body, buttons in st.after_reply_buttons:
        if st.booked and buttons[0].id.startswith("slot:"):
            continue  # booked in this turn – the time buttons are stale
        await outbound.send_buttons(db, user, body, buttons)


def _slot_button_text(button_id: str, title: str) -> str:
    if button_id == "slot:other":
        return "Could you show me other times?"
    start, doctor, service = button_id.removeprefix("slot:").split("|")
    return f"Yes, please book {title.removeprefix('Book ')}. [slot={start} provider={doctor} service={service}]"


async def _handle_button(db: AsyncSession, user: User, button_id: str) -> bool:
    if button_id.startswith("consent:"):
        if button_id == "consent:no":
            user.onboarding_state = "declined"
            user.health_consent, user.proactive_opt_in = False, False
            await outbound.send_text(
                db, user,
                "No problem. Whenever you're ready, just send me a message and tap *I Agree* to get started 🌿\n\n"
                "For medical concerns, contact your healthcare provider directly. In an emergency, call 911.",
                sender="aster_system",
            )
            return True
        user.onboarding_state = "active"
        user.health_consent, user.proactive_opt_in = True, True
        deferred = list(await db.scalars(
            select(Message).where(Message.user_id == user.id, Message.status == "deferred").order_by(Message.created_at)
        ))
        text = "\n".join(m.body for m in deferred if m.body).strip()
        if deferred and len(text) > 12:  # more than a greeting – answer it now
            await outbound.send_text(db, user, "💚 *You're all set!*\n\nI'm excited to be part of your wellness "
                                     "journey!", sender="aster_system")
            await _run_main_agent(db, user, deferred, text, safety.prefilter(text))
        else:
            await outbound.send_text(db, user, "💚 *You're all set!*\n\nI'm excited to be part of your wellness "
                                     "journey!\n\nHow can I help you today?", sender="aster_system")
        for m in deferred:
            m.status = "processed"
        return True

    if button_id.startswith("pa:"):
        _, verdict, action_id = button_id.split(":", 2)
        action = await db.get(PendingAction, uuid.UUID(action_id))
        if not action or action.user_id != user.id or action.status != "awaiting_user":
            await outbound.send_text(db, user, "That confirmation has expired – could you tell me the details again?",
                                     sender="aster_system")
            return True
        if verdict == "no":
            action.status = "rejected"
            await outbound.send_text(db, user, "No problem — what should I change?", sender="aster_system")
            return True
        action.status = "confirmed"
        journey = await journeys.commit_journey(db, user, dict(action.payload))
        planned = await plan_for_journey(db, user, journey)
        when = ""
        if planned:
            nxt = planned[0]
            day_word = "tomorrow" if nxt.day_offset == journeys.day_post_treatment(user, journey) + 1 else \
                f"on Day {nxt.day_offset} ({nxt.due_at.astimezone(_tz(user)).strftime('%A')})"
            when = f" I'll check in on how you're feeling {day_word}."
        await outbound.send_text(
            db, user,
            f"Saved to your journey 💛{when} You can message me anytime with updates, and reply STOP to pause check-ins.",
            sender="aster_system",
        )
        return True
    return False


def _tz(user: User):
    from zoneinfo import ZoneInfo

    return ZoneInfo(user.timezone)


# ---------- proactive check-in opener ----------


async def run_checkin_opener(db: AsyncSession, user: User, checkin: Checkin) -> bool:
    journey = await db.get(journeys.Journey, checkin.journey_id) if checkin.journey_id else None
    run = AgentRun(user_id=user.id, kind="checkin_opener", provider="", model="", prompt_version="")
    db.add(run)
    await db.flush()
    st = TurnState(db=db, user=user, journey=journey, checkin=None, agent_run=run, source_message_ids=[])
    ctx = await build_context_block(db, user, journey, checkin, [])
    event = (f"{ctx}\n\n<event>It is time for the scheduled Day {checkin.day_offset} recovery check-in. Write the "
             "opening check-in message to the patient: greet them by first name, say it's Aster checking in after "
             "their treatment and which day it is, refer to anything relevant they reported before, and ask about "
             "the protocol's must_ask items (the red_flag_probes can come in the next message). Keep it short."
             "</event>")
    history = await build_history(db, user, exclude_ids=set())
    result = await run_agent(st, history, event, kind="checkin_opener", allow_tools=False)
    if result.error or not result.messages:
        name = user.first_name or "there"
        result.messages = [f"Hi {name}! 💛 It's Aster, checking in after your treatment. How are you feeling today?"]
    run.output = {"messages": result.messages}
    ok = True
    for msg in result.messages:
        ok = await outbound.send_text(db, user, msg, agent_run_id=run.id, checkin_id=checkin.id) and ok
    return ok
