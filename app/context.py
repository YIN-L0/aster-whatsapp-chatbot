"""Builds the per-turn context. Structured data is the source of truth; raw history is only the last few turns."""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import json

from app import assessment_engine, booking, companion, journeys, memory, places, referral
from app.channels import is_web_user
from app.config import get_settings
from app.handoff import open_handoff
from app.models import Checkin, Journey, Message, ProviderLink, User

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
        elif m.type == "list":
            body = f"{m.body}\n[menu shown: {', '.join(r['title'] for r in (m.payload or {}).get('rows', []))}]"
        elif m.type == "location_request":
            body = f"{m.body}\n[Send location button shown]"
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


async def _links(db: AsyncSession, user: User) -> list[str]:
    rows = await db.scalars(select(ProviderLink).where(ProviderLink.user_id == user.id, ProviderLink.status != "declined"))
    return [f"{link.provider_slug} ({link.status})" for link in rows]


def _fmt_list(items: list) -> str:
    return "; ".join(str(i) for i in items) if items else "(none)"


async def build_context_block(
    db: AsyncSession, user: User, journey: Journey | None, checkin: Checkin | None, rule_negated: list[str],
    returning: bool = False,
) -> str:
    tz = ZoneInfo(user.timezone)
    now = datetime.now(tz)
    lines = [
        "<context>",
        f"now_local: {now.strftime('%A %Y-%m-%d %H:%M')} ({user.timezone})",
        f"patient: first_name={user.first_name or 'unknown'}; health_consent={str(user.health_consent).lower()}",
        f"aster: active_mode={user.active_mode or 'none'}; entry_source={user.entry_source or 'unknown'}; "
        f"provider_links={', '.join(await _links(db, user)) or 'none'}; location={places.label(user) or 'unknown'}",
    ]
    if returning:
        lines.append("RETURNING: this is their first message after a long break – a short welcome back fits, then "
                     "answer what they wrote.")
    lines.append(await mode_block(db, user, journey, checkin))
    declined = await referral.declined_needs(db, user)
    if declined:
        lines.append(f"REFERRALS: they said no to options for {', '.join(declined)} – don't offer these again")
    handoff = await open_handoff(db, user.id)
    if handoff:
        lines.append(f"OPEN HANDOFF: {handoff.urgency} ({handoff.reason}) – the team has already been alerted")
    if rule_negated:
        lines.append(f"SAFETY NOTE: patient mentioned but appeared to deny: {', '.join(rule_negated)}")
    lines.append("</context>")
    return "\n".join(lines)


async def mode_block(db: AsyncSession, user: User, journey: Journey | None, checkin: Checkin | None) -> str:
    """What the active mode needs. Recovery details only in post_treatment, so the companion stays about the user."""
    if user.active_mode == "post_treatment":
        return await _post_treatment_block(db, user, journey, checkin)
    tz = ZoneInfo(user.timezone)
    lines = ["COMPANION:"]
    goals = await memory.goals(db, user)
    lines.append("  goals: " + (_fmt_list([f"{g.code}{' (focus)' if g.is_focus else ''}"
                                          + (f" – \"{g.label_raw}\"" if g.label_raw else "") for g in goals])))
    focus = next((g for g in goals if g.is_focus), None)
    if focus and not companion.is_structured(focus.code):
        lines.append(f"  focus goal {focus.code} is chat only for now: general guidance, no structured assessment "
                     "or plan yet (a fuller plan for it is coming later)")
    plan = await memory.active_plan(db, user)
    lines.append(f"  plan: {f'{plan.goal} v{plan.version} from {plan.created_at:%Y-%m-%d} ({plan.confidence})' if plan else 'none yet'}")
    facts = await memory.load_facts(db, user)
    now = datetime.now(tz)
    if facts:
        lines.append("  PROFILE (what they told you – don't ask again):")
        lines += [f"    {k}: {json.dumps(f.value, separators=(',', ':'))[:160]} ({(now - f.recorded_at).days}d ago)"
                  for k, f in facts.items()]
    a = await memory.current_assessment(db, user)
    if a:
        skipped = set(a.skipped_slots)
        r = companion.readiness(a.goal, facts, skipped, now)
        lines.append(f"  ASSESSMENT: goal={a.goal}; status={a.status}; readiness={r['level']} (core "
                     f"{r.get('core_known', 0)}/{r.get('core_total', 0)}, supporting {r.get('supporting_known', 0)}/"
                     f"{r.get('supporting_total', 0)}); skipped={_fmt_list(sorted(skipped))}")
        if a.status == "paused":
            lines.append("    paused – offer to continue only if it fits what they're saying; don't push")
        nxt = companion.next_slot(a.goal, facts, skipped, now)
        if nxt:
            lines.append(f"    NEXT QUESTION: slot={nxt['slot']}; mode={nxt['mode']}; purpose={nxt['purpose']} "
                         f"ask_hint={nxt['ask_hint']} examples={' | '.join(nxt['examples'])}"
                         + (f"; previous answer={json.dumps(nxt['previous'])}" if nxt["previous"] else ""))
            if nxt["slot"] == "meals":
                lines.append("    FOOD ESTIMATES (for kcal_est only, never quote exact numbers): "
                             + "; ".join(assessment_engine.rules()["diet"]["quick_estimates"]))
        else:
            lines.append("    every question is answered or skipped")
    if journey:
        lines.append(f"  essence treatment on file: {journey.treatment_label_raw} on {journey.treatment_date} "
                     f"(Day {journeys.day_post_treatment(user, journey)}) – details are in post_treatment mode")
    appts = await booking.upcoming_appointments(db, user.id)
    if appts:
        lines.append("  upcoming Essence visits: " + "; ".join(
            f"{a.purpose} {booking.fmt_slot(a.starts_at.astimezone(tz))}" for a in appts))
    return "\n".join(lines)


async def _post_treatment_block(db: AsyncSession, user: User, journey: Journey | None, checkin: Checkin | None) -> str:
    tz = ZoneInfo(user.timezone)
    lines = ["PATIENT_DETAILS still needed before booking: needed=" + (", ".join(_missing_booking_details(user)) or "none")]
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
    return "\n".join(lines)
