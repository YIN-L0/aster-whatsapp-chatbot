"""Treatment Journey, Recovery Journal and symptom memory."""

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Journey, JournalEntry, PendingAction, SymptomObservation, User

PROTOCOL_DIR = Path(__file__).parent / "protocols"

SYMPTOM_CODES = [
    "swelling", "bruising", "pain", "tenderness", "soreness", "redness", "warmth", "itching", "numbness",
    "nausea", "vomiting", "constipation", "diarrhea", "appetite_reduced", "dizziness", "headache", "fatigue",
    "fever", "abdominal_pain", "injection_site_reaction", "lumps", "asymmetry_concern", "skin_discoloration",
    "vision_change", "breathing_difficulty", "tingling", "flushing", "swallowing_difficulty", "eyelid_droop",
    "unable_to_keep_fluids", "dark_urine", "other",
]
SEVERITY_ORDER = {"none": 0, "mild": 1, "moderate": 2, "severe": 3}


# ---------- protocols ----------


@lru_cache
def load_protocols() -> dict[str, dict]:
    protocols = {}
    for path in PROTOCOL_DIR.glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        protocols[data["slug"]] = data
    return protocols


def get_protocol(slug: str) -> dict:
    protocols = load_protocols()
    return protocols.get(slug, protocols["generic"])


def match_treatment(text: str) -> str:
    t = text.lower()
    best, best_len = "generic", 0
    for slug, proto in load_protocols().items():
        for alias in proto.get("aliases", []):
            if re.search(rf"\b{re.escape(alias)}\b", t) and len(alias) > best_len:
                best, best_len = slug, len(alias)
    return best


# ---------- time helpers ----------


def local_today(user: User) -> date:
    return datetime.now(ZoneInfo(user.timezone)).date()


def day_post_treatment(user: User, journey: Journey) -> int:
    return (local_today(user) - journey.treatment_date).days


# ---------- journeys ----------


async def get_primary_journey(db: AsyncSession, user_id: uuid.UUID) -> Journey | None:
    return await db.scalar(
        select(Journey)
        .where(Journey.user_id == user_id, Journey.is_primary.is_(True), Journey.status == "active")
        .order_by(Journey.created_at.desc())
    )


async def list_journeys(db: AsyncSession, user_id: uuid.UUID) -> list[Journey]:
    return list(
        await db.scalars(select(Journey).where(Journey.user_id == user_id).order_by(Journey.treatment_date.desc()))
    )


async def create_pending_journey(
    db: AsyncSession, user: User, payload: dict, agent_run_id: uuid.UUID | None
) -> PendingAction:
    await db.execute(
        update(PendingAction)
        .where(PendingAction.user_id == user.id, PendingAction.status == "awaiting_user")
        .values(status="superseded")
    )
    action = PendingAction(
        user_id=user.id,
        kind=payload.pop("kind", "create_journey"),
        payload=payload,
        agent_run_id=agent_run_id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.add(action)
    await db.flush()
    return action


def validate_treatment_date(user: User, value: str) -> date:
    d = date.fromisoformat(value)
    today = local_today(user)
    if d > today + timedelta(days=60):
        raise ValueError("treatment_date is too far in the future")
    if d < today - timedelta(days=365):
        raise ValueError("treatment_date is more than a year ago")
    return d


def confirmation_text(payload: dict) -> str:
    proto = get_protocol(payload["treatment_slug"])
    d = date.fromisoformat(payload["treatment_date"])
    lines = ["Just to confirm, I'll save this to your treatment journey:", ""]
    lines.append(f"• Treatment: {payload.get('treatment_text') or proto['display_name']}")
    lines.append(f"• Date: {d.strftime('%a, %b')} {d.day}")
    if payload.get("area"):
        lines.append(f"• Area: {payload['area']}")
    if payload.get("provider_name"):
        lines.append(f"• Provider: {payload['provider_name']}")
    return "\n".join(lines)


async def commit_journey(db: AsyncSession, user: User, payload: dict, source: str = "user_reported") -> Journey:
    """Deterministic write – only called after the user taps Confirm (or by demo seeding)."""
    if payload.get("journey_id"):
        journey = await db.get(Journey, uuid.UUID(payload["journey_id"]))
        if journey and journey.user_id == user.id:
            journey.treatment_date = date.fromisoformat(payload["treatment_date"])
            journey.treatment_slug = payload["treatment_slug"]
            journey.treatment_label_raw = payload.get("treatment_text") or journey.treatment_label_raw
            journey.area = payload.get("area") or journey.area
            journey.provider_name = payload.get("provider_name") or journey.provider_name
            await db.flush()
            return journey
    await db.execute(update(Journey).where(Journey.user_id == user.id).values(is_primary=False))
    proto = get_protocol(payload["treatment_slug"])
    journey = Journey(
        user_id=user.id,
        treatment_slug=payload["treatment_slug"],
        treatment_label_raw=payload.get("treatment_text") or proto["display_name"],
        treatment_date=date.fromisoformat(payload["treatment_date"]),
        area=payload.get("area"),
        product=payload.get("product"),
        provider_name=payload.get("provider_name"),
        protocol_version=proto["version"],
        source=source,
    )
    db.add(journey)
    await db.flush()
    return journey


# ---------- recovery journal ----------


async def record_recovery_update(
    db: AsyncSession,
    user: User,
    journey: Journey,
    *,
    summary: str,
    patient_quote: str | None,
    mood: str | None,
    observations: list[dict],
    risk_level: int,
    checkin_id: uuid.UUID | None,
    agent_run_id: uuid.UUID | None,
    source_message_ids: list[str],
) -> JournalEntry:
    day = day_post_treatment(user, journey)
    entry = JournalEntry(
        user_id=user.id,
        journey_id=journey.id,
        checkin_id=checkin_id,
        day_post_treatment=day,
        summary=summary,
        patient_quote=patient_quote,
        mood=mood,
        risk_level=risk_level,
        agent_run_id=agent_run_id,
        source_message_ids=source_message_ids,
    )
    db.add(entry)
    await db.flush()
    previous = await latest_observations(db, journey.id)
    for obs in observations:
        code = obs.get("symptom_code") if obs.get("symptom_code") in SYMPTOM_CODES else "other"
        db.add(
            SymptomObservation(
                journal_entry_id=entry.id,
                journey_id=journey.id,
                symptom_code=code,
                present=bool(obs.get("present", True)),
                severity=obs.get("severity"),
                frequency=obs.get("frequency"),
                body_area=obs.get("body_area"),
                status_change=obs.get("status_change"),
                computed_trend=compute_trend(previous.get(code), obs),
                free_text=obs.get("free_text"),
                day_post_treatment=day,
            )
        )
    await db.flush()
    return entry


def _sev(present: bool, severity: str | None) -> int | None:
    if not present:
        return 0
    return SEVERITY_ORDER.get(severity or "", None)


def compute_trend(prev: SymptomObservation | None, obs: dict) -> str | None:
    if prev is None:
        return "new" if obs.get("present", True) else None
    before = _sev(prev.present, prev.severity)
    after = _sev(bool(obs.get("present", True)), obs.get("severity"))
    if before is None or after is None:
        return obs.get("status_change")
    if after == 0 and before > 0:
        return "resolved"
    if after > before:
        return "worsening"
    if after < before:
        return "improving"
    return "same"


async def latest_observations(db: AsyncSession, journey_id: uuid.UUID) -> dict[str, SymptomObservation]:
    rows = await db.scalars(
        select(SymptomObservation)
        .where(SymptomObservation.journey_id == journey_id)
        .order_by(SymptomObservation.created_at)
    )
    latest: dict[str, SymptomObservation] = {}
    for r in rows:
        latest[r.symptom_code] = r
    return latest


async def symptom_timeline(db: AsyncSession, journey_id: uuid.UUID) -> str:
    """Compact table injected into the prompt. Trends are computed in code, not by the LLM."""
    rows = list(
        await db.scalars(
            select(SymptomObservation)
            .where(SymptomObservation.journey_id == journey_id)
            .order_by(SymptomObservation.created_at)
        )
    )
    if not rows:
        return "(no symptoms recorded yet)"
    lines = []
    for r in rows:
        state = "absent" if not r.present else (r.severity or "present")
        extra = ", ".join(x for x in [r.frequency, r.body_area, r.free_text] if x)
        trend = f" [{r.computed_trend}]" if r.computed_trend else ""
        lines.append(f"Day {r.day_post_treatment}: {r.symptom_code} = {state}{trend}" + (f" ({extra})" if extra else ""))
    return "\n".join(lines[-40:])


async def has_worsening(db: AsyncSession, journal_entry_id: uuid.UUID) -> bool:
    rows = await db.scalars(
        select(SymptomObservation).where(
            SymptomObservation.journal_entry_id == journal_entry_id,
            SymptomObservation.computed_trend == "worsening",
        )
    )
    return rows.first() is not None
