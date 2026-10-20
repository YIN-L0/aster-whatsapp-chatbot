"""Human handoff: record, summarise for staff (facts from the DB, not the LLM), notify."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.journeys import day_post_treatment, get_primary_journey, get_protocol, symptom_timeline
from app.models import Handoff, Message, User, utcnow
from app.outbound import notify_staff

URGENCY_FOR_RISK = {2: "routine", 3: "urgent", 4: "emergency"}


async def open_handoff(db: AsyncSession, user_id: uuid.UUID) -> Handoff | None:
    return await db.scalar(
        select(Handoff).where(Handoff.user_id == user_id, Handoff.status == "open").order_by(Handoff.created_at.desc())
    )


async def create_handoff(
    db: AsyncSession, user: User, *, reason: str, urgency: str, note: str, signals: dict | None = None
) -> Handoff:
    existing = await open_handoff(db, user.id)
    rank = {"routine": 0, "urgent": 1, "emergency": 2}
    if existing and rank[existing.urgency] >= rank[urgency]:
        existing.summary += f"\n+ {note}"
        # A new reason (e.g. a dose question after a symptom review) gets its own short alert, once per reason,
        # so staff don't miss it inside an alert they already read. Auto risk merges (risk_lN) at the same or lower
        # urgency add nothing new for staff and stay silent.
        signals_ = dict(existing.risk_signals or {})
        seen = signals_.get("alerted_reasons", [existing.reason])
        if reason not in seen and not reason.startswith("risk_l"):
            signals_["alerted_reasons"] = seen + [reason]
            existing.risk_signals = signals_
            name = user.first_name or user.display_name or "Unknown"
            await notify_staff(f"➕ Update · Aster handoff · {'TEST USER' if user.is_test_user else ''}\n"
                               f"Patient: {name} · +{user.wa_id}\nNew: {reason} — {note}")
        await db.flush()
        return existing

    journey = await get_primary_journey(db, user.id)
    header = [
        f"{'🚨 EMERGENCY' if urgency == 'emergency' else '⚠️ URGENT' if urgency == 'urgent' else '📋 Review'}"
        f" · Aster handoff · {'TEST USER' if user.is_test_user else ''}",
        f"Patient: {user.first_name or user.display_name or 'Unknown'} · +{user.wa_id}",
    ]
    if journey:
        proto = get_protocol(journey.treatment_slug)
        header.append(
            f"Treatment: {journey.treatment_label_raw or proto['display_name']} · "
            f"{journey.treatment_date.isoformat()} (Day {day_post_treatment(user, journey)})"
        )
        header.append("Symptom timeline:\n" + await symptom_timeline(db, journey.id))
    header.append(f"Reason: {reason} — {note}")
    recent = list(
        await db.scalars(
            select(Message)
            .where(Message.user_id == user.id, Message.direction == "in")
            .order_by(Message.created_at.desc())
            .limit(3)
        )
    )
    if recent:
        header.append("Last messages:\n" + "\n".join(f'  "{m.body[:200]}"' for m in reversed(recent)))
    summary = "\n".join(header)

    if existing:
        existing.status = "superseded"
    handoff = Handoff(
        user_id=user.id,
        journey_id=journey.id if journey else None,
        reason=reason,
        urgency=urgency,
        summary=summary,
        risk_signals=signals,
    )
    db.add(handoff)
    if urgency in ("urgent", "emergency"):
        user.conversation_mode = "human"
    await db.flush()
    await notify_staff(summary)
    handoff.notified = True
    return handoff


async def resolve_all(db: AsyncSession, user: User) -> int:
    result = await db.execute(
        update(Handoff)
        .where(Handoff.user_id == user.id, Handoff.status == "open")
        .values(status="resolved", resolved_at=utcnow())
    )
    user.conversation_mode = "ai"
    return result.rowcount or 0
