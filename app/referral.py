"""Provider referral gate (docs/COMPANION_REDESIGN.md §6.4). Code, not the prompt, decides when providers may appear.

- Aster may *offer* options for a need at most once per 30 days, and never again within 30 days of a "no".
- Aster may *show* options only when the user asked for help / a service in this turn, or accepted an offer.
- Every offer, answer and list of options shown is logged in referral_offers.
"""

import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import memory, providers
from app.models import ReferralOffer, User, utcnow

COOLDOWN = timedelta(days=30)
ACCEPT_WINDOW = timedelta(hours=24)
ANY_NEED = "any"  # "Explore Health Services" in the menu: the user asked, the need comes next
ASK = re.compile(
    r"\b(recommend\w*|suggest\w*|some(one|body)|professional|expert|specialist|dietitian|nutritionist|coach(ing)?|"
    r"trainer|doctor|clinic(ian)?|service|provider|program|options?|where (can|do|should) i|who (can|could|should)|"
    r"find (a|an|me|someone)|book|appointment|consult\w*|therapist|med ?spa|partner)\b", re.I)


async def _rows(db: AsyncSession, user: User, need: str | None = None) -> list[ReferralOffer]:
    q = select(ReferralOffer).where(ReferralOffer.user_id == user.id, ReferralOffer.created_at > utcnow() - COOLDOWN)
    if need:
        q = q.where(ReferralOffer.need == need)
    return list(await db.scalars(q.order_by(ReferralOffer.created_at.desc())))


async def declined_needs(db: AsyncSession, user: User) -> list[str]:
    return sorted({r.need for r in await _rows(db, user) if r.status == "declined"})


async def can_offer(db: AsyncSession, user: User, need: str) -> str | None:
    """None if Aster may ask "Would you like some options?", else why not."""
    rows = await _rows(db, user, need)
    if any(r.status == "declined" for r in rows):
        return "declined_recently"
    if rows:
        return "already_offered"
    return None


async def may_show(db: AsyncSession, user: User, need: str, user_text: str) -> str | None:
    """None if options may be shown now, else why not. Asking explicitly overrides an earlier "no"."""
    if ASK.search(user_text or ""):
        return None
    recent = [r for r in await _rows(db, user) if r.need in (need, ANY_NEED) and r.status == "accepted"
              and utcnow() - r.created_at < ACCEPT_WINDOW]
    return None if recent else "user_has_not_asked"


async def log(db: AsyncSession, user: User, need: str, status: str, *, shown: list[str] | None = None,
              mode: str | None = None) -> ReferralOffer:
    row = ReferralOffer(user_id=user.id, need=need, status=status, providers_shown=shown, source_mode=mode)
    if status in ("accepted", "declined"):
        row.decided_at = utcnow()
    db.add(row)
    await db.flush()
    return row


async def share_summary(db: AsyncSession, user: User, need: str) -> str:
    """What the user agrees to share: their goal, what they're looking for and their plan's priorities.
    Never profile numbers, symptoms or anything sensitive."""
    goal = await memory.focus_goal(db, user)
    plan = await memory.active_plan(db, user)
    lines = [f"Name: {user.first_name or 'not given'}",
             f"Looking for: {providers.NEEDS.get(need, need)}"]
    if goal:
        lines.append(f"Goal: {goal.label_raw or goal.code.replace('_', ' ')}")
    if plan:
        lines.append("Current plan priorities: " + "; ".join(plan.content.get("priorities", [])))
    return "\n".join(lines)
