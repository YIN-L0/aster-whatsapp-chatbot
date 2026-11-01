"""Long-term memory: profile facts, goals, assessment progress and plans (docs/COMPANION_REDESIGN.md §7).

Every answer is stored as soon as it is given, so nothing is lost when the user leaves, switches modes or starts a
new assessment. Decisions (next question, readiness, plan checks) are pure functions in app.companion.
"""

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import companion
from app.models import CompanionAssessment, Plan, ProfileFact, User, UserGoal, utcnow

ABANDON_AFTER = timedelta(days=60)


async def load_facts(db: AsyncSession, user: User) -> dict[str, companion.Fact]:
    rows = await db.scalars(select(ProfileFact).where(ProfileFact.user_id == user.id, ProfileFact.superseded_at.is_(None)))
    return {r.key: companion.Fact(r.value, r.recorded_at) for r in rows}


async def save_fact(db: AsyncSession, user: User, slot: str, clean: dict, *, agent_run_id: uuid.UUID | None = None,
                    source_message_ids: list[str] | None = None) -> ProfileFact:
    """Stores a new current value; the old row stays as history. A partial answer ("I'm 5'6\"") is merged with a
    still-fresh earlier one ("165 lb") instead of replacing it."""
    now = utcnow()
    old = await db.scalar(select(ProfileFact).where(ProfileFact.user_id == user.id, ProfileFact.key == slot,
                                                    ProfileFact.superseded_at.is_(None)))
    value = dict(clean)
    if old:
        ttl = companion.rules()["slots"][slot]["ttl_days"]
        if not ttl or now - old.recorded_at < timedelta(days=ttl):
            value = {**old.value, **clean}
        old.superseded_at = now
    fact = ProfileFact(user_id=user.id, key=slot, value=value, recorded_at=now, agent_run_id=agent_run_id,
                       source_message_ids=source_message_ids)
    db.add(fact)
    await db.flush()
    return fact


async def goals(db: AsyncSession, user: User) -> list[UserGoal]:
    return list(await db.scalars(select(UserGoal).where(UserGoal.user_id == user.id, UserGoal.status == "active")
                                 .order_by(UserGoal.created_at)))


async def focus_goal(db: AsyncSession, user: User) -> UserGoal | None:
    return next((g for g in await goals(db, user) if g.is_focus), None)


async def set_goal(db: AsyncSession, user: User, code: str, label_raw: str | None = None) -> UserGoal:
    """Makes `code` the focus goal. Other goals stay remembered."""
    goal = None
    for g in await goals(db, user):
        g.is_focus = g.code == code
        if g.code == code:
            goal = g
    if goal is None:
        goal = UserGoal(user_id=user.id, code=code, is_focus=True)
        db.add(goal)
    if label_raw:
        goal.label_raw = label_raw[:200]
    await db.flush()
    return goal


async def open_assessment(db: AsyncSession, user: User) -> CompanionAssessment | None:
    a = await db.scalar(
        select(CompanionAssessment)
        .where(CompanionAssessment.user_id == user.id, CompanionAssessment.status.in_(("in_progress", "paused")))
        .order_by(CompanionAssessment.created_at.desc())
    )
    if a and utcnow() - a.updated_at > ABANDON_AFTER:
        a.status = "abandoned"
        return None
    return a


async def current_assessment(db: AsyncSession, user: User) -> CompanionAssessment | None:
    """The open assessment, or else the latest one with a plan (for updates)."""
    return await open_assessment(db, user) or await db.scalar(
        select(CompanionAssessment).where(CompanionAssessment.user_id == user.id,
                                          CompanionAssessment.status == "plan_active")
        .order_by(CompanionAssessment.created_at.desc())
    )


async def start_assessment(db: AsyncSession, user: User, goal: str) -> tuple[CompanionAssessment, bool]:
    """Returns (assessment, is_new). A different goal pauses the open one; facts are shared, so nothing is re-asked."""
    a = await current_assessment(db, user)
    if a and a.goal == goal:
        if a.status == "paused":
            a.status = "in_progress"
        return a, False
    if a and a.status == "in_progress":
        a.status = "paused"
    a = CompanionAssessment(user_id=user.id, goal=goal, rules_version=companion.rules()["version"])
    db.add(a)
    await db.flush()
    return a, True


async def pause_open_assessment(db: AsyncSession, user: User) -> None:
    a = await open_assessment(db, user)
    if a and a.status == "in_progress":
        a.status = "paused"


async def active_plan(db: AsyncSession, user: User) -> Plan | None:
    return await db.scalar(select(Plan).where(Plan.user_id == user.id, Plan.status == "active")
                           .order_by(Plan.version.desc()))


async def save_plan(db: AsyncSession, user: User, assessment: CompanionAssessment, draft: dict, skeleton: dict,
                    fact_ids: list[str], agent_run_id: uuid.UUID | None) -> Plan:
    """Stores a new version and supersedes the previous plan."""
    last = await active_plan(db, user)
    version = 1
    if last:
        last.status = "superseded"
        version = last.version + 1
    content = {k: draft[k] for k in ("situation_summary", *companion.PLAN_LISTS)}
    content["limitations"] = skeleton["limitations"]
    content["cautions"] = skeleton["cautions"]
    plan = Plan(user_id=user.id, assessment_id=assessment.id, goal=assessment.goal, version=version,
                confidence=skeleton["confidence"], content=content,
                review_after=(utcnow() + timedelta(days=draft["review_in_days"])).date(),
                based_on=fact_ids, rules_version=skeleton["rules_version"], agent_run_id=agent_run_id)
    db.add(plan)
    assessment.status = "plan_active"
    await db.flush()
    return plan


async def current_fact_ids(db: AsyncSession, user: User) -> list[str]:
    rows = await db.scalars(select(ProfileFact.id).where(ProfileFact.user_id == user.id,
                                                         ProfileFact.superseded_at.is_(None)))
    return [str(i) for i in rows]
