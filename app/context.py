"""Builds the per-turn context. Structured data is the source of truth; raw history is only the last few turns."""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import booking, journeys
from app.channels import is_web_user
from app.config import get_settings
from app.handoff import open_handoff
from app.models import Checkin, Journey, Message, User

HISTORY_LIMIT = 20


async def build_history(db: AsyncSession, user: User, exclude_ids: set) -> list[dict]:
    rows = list(
        await db.scalars(
            select(Message)
            .where(Message.user_id == user.id, Message.type != "system")
            .order_by(Message.created_at.desc())
            .limit(HISTORY_LIMIT + len(exclude_ids))
        )
    )
    turns: list[dict] = []
    for m in reversed(rows):
        if m.id in exclude_ids or not m.body:
            continue
        role = "user" if m.direction == "in" else "assistant"
        body = m.body
        if m.type == "button_reply":
            body = f"[tapped button: {m.body}]"
        elif m.type == "buttons":
            body = f"{m.body}\n[buttons shown: {', '.join(b['title'] for b in (m.payload or {}).get('buttons', []))}]"
        elif m.type == "template":
            body = "[sent check-in template message]"
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n\n" + body
        else:
            turns.append({"role": role, "content": body})
    if turns and turns[0]["role"] == "assistant":
        turns.insert(0, {"role": "user", "content": "(conversation started)"})
    if turns and turns[-1]["role"] == "user":
        # Unanswered earlier user message – merge it into a placeholder assistant turn boundary.
        turns.append({"role": "assistant", "content": "(no reply was sent)"})
    return turns


async def next_checkin(db: AsyncSession, user: User) -> Checkin | None:
    return await db.scalar(
        select(Checkin)
        .where(Checkin.user_id == user.id, Checkin.status == "scheduled", Checkin.kind == "recovery")
        .order_by(Checkin.due_at)
    )


def _missing_booking_details(user: User) -> list[str]:
    missing = [] if user.full_name else ["full name"]
    if not user.birth_date and not get_settings().booking_default_birth_date:
        missing.append("date of birth")
    if is_web_user(user.wa_id) and not user.phone:
        missing.append("mobile number")
    return missing


def _fmt_list(items: list) -> str:
    return "; ".join(str(i) for i in items) if items else "(none)"


async def build_context_block(
    db: AsyncSession, user: User, journey: Journey | None, checkin: Checkin | None, rule_negated: list[str]
) -> str:
    tz = ZoneInfo(user.timezone)
    now = datetime.now(tz)
    lines = [
        "<context>",
        f"now_local: {now.strftime('%A %Y-%m-%d %H:%M')} ({user.timezone})",
        f"patient: first_name={user.first_name or 'unknown'}; health_consent={str(user.health_consent).lower()}",
        "PATIENT_DETAILS still needed before booking: needed=" + (", ".join(_missing_booking_details(user)) or "none"),
    ]
    if journey:
        proto = journeys.get_protocol(journey.treatment_slug)
        day = journeys.day_post_treatment(user, journey)
        lines += [
            "ACTIVE JOURNEY:",
            f"  treatment: {journey.treatment_label_raw} (protocol: {proto['display_name']})",
            f"  treatment_date: {journey.treatment_date.isoformat()} → today is Day {day} after treatment",
            f"  provider: {journey.provider_name or 'not recorded'}",
            "PROTOCOL (demo draft):",
            f"  expected_symptoms: {_fmt_list(proto.get('expected_symptoms', []))}",
            f"  review_signals (risk 2): {_fmt_list(proto.get('review_signals', []))}",
            f"  L3 red flags (risk 3): {_fmt_list(proto['red_flags'].get('L3', []))}",
            f"  L4 red flags (risk 4): {_fmt_list(proto['red_flags'].get('L4', []))}",
            f"  aftercare_notes: {_fmt_list(proto.get('aftercare_notes', []))}",
            f"  provider_followup_visit: {proto.get('provider_followup_visit') or '(none)'}",
            "SYMPTOM TIMELINE:",
            "  " + (await journeys.symptom_timeline(db, journey.id)).replace("\n", "\n  "),
        ]
        others = [j for j in await journeys.list_journeys(db, user.id) if j.id != journey.id]
        if others:
            lines.append("OTHER JOURNEYS: " + "; ".join(f"{j.treatment_label_raw} on {j.treatment_date}" for j in others[:5]))
    else:
        lines.append("ACTIVE JOURNEY: none recorded yet")

    if checkin and checkin.kind == "recovery" and checkin.day_offset is not None and journey:
        proto = journeys.get_protocol(journey.treatment_slug)
        step = next((s for s in proto["schedule"] if s["day"] == checkin.day_offset), None)
        if step:
            lines += [
                f"ACTIVE CHECK-IN: Day {checkin.day_offset} (the patient is replying to it)",
                f"  must_ask: {_fmt_list(step.get('ask', []))}",
                f"  red_flag_probes: {_fmt_list(step.get('red_flag_probes', []))}",
            ]
    nxt = await next_checkin(db, user)
    if nxt:
        lines.append(f"NEXT_CHECKIN: Day {nxt.day_offset}, around {nxt.due_at.astimezone(tz).strftime('%A %b %d')}")
    else:
        lines.append("NEXT_CHECKIN: none scheduled")
    appts = await booking.upcoming_appointments(db, user.id)
    if appts:
        lines.append("UPCOMING APPOINTMENTS: " + "; ".join(
            f"{a.purpose} {booking.fmt_slot(a.starts_at.astimezone(tz))}" for a in appts))
    handoff = await open_handoff(db, user.id)
    if handoff:
        lines.append(f"OPEN HANDOFF: {handoff.urgency} ({handoff.reason}) – the team has already been alerted")
    if rule_negated:
        lines.append(f"SAFETY NOTE: patient mentioned but appeared to deny: {', '.join(rule_negated)}")
    lines.append("</context>")
    return "\n".join(lines)
