"""Entry, menu and mode routing (docs/COMPANION_REDESIGN.md §5–6). Deterministic: no LLM call.

WhatsApp has no persistent menu, so the hub is summoned: HUB (or the older MENU) / slash commands send a list message, and tapping a
row switches active_mode. Switching never touches progress – assessments, plans and journeys live in their own tables.
"""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import journeys, memory, outbound, providers, referral
from app.channels import Button, ListRow
from app.companion import goal_spec
from app.config import get_settings
from app.handoff import create_handoff
from app.models import Appointment, CompanionAssessment, Handoff, Journey, Message, Plan, ProviderLink, ReferralOffer, User, utcnow

PREFIXES = ("entry:", "menu:", "mode:", "invite:", "ref:")  # ref:yes is turned into text for the agent first
# HUB is the word users are shown; MENU and the rest still work (older messages and habits say MENU)
HUB_WORDS = {"HUB", "/HUB", "MENU", "/MENU", "HOME", "SWITCH", "BACK", "GO BACK"}
SLASH = {"/companion": "mode:companion", "/aftercare": "mode:post_treatment", "/treatment": "mode:post_treatment",
         "/goals": "menu:goals", "/plan": "menu:plan", "/services": "menu:services", "/human": "menu:human", "/privacy": "menu:privacy"}
# WhatsApp ice breakers (shown on an empty chat) arrive as plain text: route them like the matching button.
# Keep in sync with scripts/setup_whatsapp_commands.py. "What is Aster?" is left to the agent.
ICE_BREAKERS = {"help me with my health goals": "mode:companion", "questions after my treatment": "mode:post_treatment"}
GREETING = re.compile(r"^\s*(hi+|hello|hey|hiya|yo|good (morning|afternoon|evening))( aster)?[\s!.,💛👋🙂]*$", re.I)

TRIAGE_TEXT = ("One quick question so I can help in the right way: have you had care or treatment at "
               "*Essence Aesthetics & Wellness* before?")
TRIAGE_BUTTONS = [Button("entry:essence", "Yes, Essence client"), Button("entry:direct", "No, set health goals"),
                  Button("entry:skip", "Skip for now")]
START_BUTTONS = [Button("mode:post_treatment", "My treatment"), Button("mode:companion", "My health goals")]
COMPANION_INTRO = (
    "I'm Aster, here to help you understand your health, create a personalized longevity plan, and connect you with "
    "the right care and services.\n\n"
    "From nutrition and fitness to preventive care and healthy aging, I'll guide you every step of the way.\n\n"
    "✨ What's your biggest health or longevity goal right now?"
)
HUB_HINT = "_Type HUB anytime to go back._"
FIRST_GOALS_TEXT = f"{COMPANION_INTRO}\n\n{HUB_HINT}"  # the first goal list a user sees
PRIVACY_TEXT = (
    "🔒 *Privacy & settings*\n\n"
    "• *STOP* pauses check-in messages; *START* turns them back on.\n"
    "• *DELETE MY DATA* asks the team to delete everything Aster has stored about you.\n"
    "• Aster never sends you marketing messages unless you ask for them.\n"
    "• Partner providers (like clinics) only see what you choose to share with them.\n\n"
    "Privacy Policy: {url}"
)


def is_router_input(button_id: str | None, text: str) -> bool:
    if button_id:
        return button_id.startswith(PREFIXES)
    t = text.strip()
    return t.upper() in HUB_WORDS or t.lower() in SLASH or t.lower() in ICE_BREAKERS


# ---------- entry ----------


def entry_from(messages: list[Message]) -> str | None:
    """A provider slug from web chat ?src=, a click-to-WhatsApp ad, or a code in the message text."""
    for m in messages:
        p = m.payload or {}
        if providers.get(p.get("entry")):
            return p["entry"]
        slug = providers.find_entry(m.body) or providers.find_entry(p.get("referral"))
        if slug:
            return slug
    return None


async def provider_links(db: AsyncSession, user: User) -> dict[str, ProviderLink]:
    rows = await db.scalars(select(ProviderLink).where(ProviderLink.user_id == user.id,
                                                       ProviderLink.status != "declined"))
    return {link.provider_slug: link for link in rows}


async def add_link(db: AsyncSession, user: User, slug: str, status: str, source: str) -> None:
    link = await db.scalar(select(ProviderLink).where(ProviderLink.user_id == user.id,
                                                      ProviderLink.provider_slug == slug))
    if link is None:
        db.add(ProviderLink(user_id=user.id, provider_slug=slug, status=status, source=source))
    elif link.status == "declined":
        link.status, link.source = status, source
    if user.entry_source in (None, "unknown", "skipped"):
        user.entry_source = slug
    await db.flush()


async def backfill_essence_links(db: AsyncSession) -> int:
    """Users from before the companion (Essence-only) era have journeys or bookings but no provider link.
    Give them one at startup so they keep treatment support. Idempotent."""
    linked = select(ProviderLink.user_id).where(ProviderLink.provider_slug == "essence")
    ids = set(await db.scalars(select(Journey.user_id).where(Journey.user_id.not_in(linked))))
    ids |= set(await db.scalars(select(Appointment.user_id).where(Appointment.user_id.not_in(linked))))
    for user in await db.scalars(select(User).where(User.id.in_(ids))) if ids else []:
        db.add(ProviderLink(user_id=user.id, provider_slug="essence", status="verified", source="legacy"))
        if user.entry_source in (None, "unknown"):
            user.entry_source = "essence"
    await db.flush()
    return len(ids)


async def after_consent(db: AsyncSession, user: User) -> None:
    """The first step after I Agree, by where the user came from. An Essence client still chooses what to talk about."""
    if user.entry_source == "essence":
        await outbound.send_buttons(db, user, "Welcome from Essence! What would you like to start with? I can help "
                                    "with questions after a treatment, or with your own health goals.", START_BUTTONS)
    elif providers.get(user.entry_source):
        user.active_mode = "companion"
        name = providers.get(user.entry_source)["name"]
        await send_goal_list(db, user, f"Welcome from {name}! 🌿\n\n{COMPANION_INTRO}\n\n{HUB_HINT}")
    else:
        await outbound.send_buttons(db, user, TRIAGE_TEXT, TRIAGE_BUTTONS)


# ---------- menu and modes ----------


open_assessment = memory.open_assessment
GOAL_ROWS = [  # (id goal:<code>, title, description before it's started) – the agent receives "My goal: <title>"
    # "weight" is not a goal code: the agent asks whether they want to lose, keep or reshape, then calls set_goal
    ("weight", "Weight management", "Lose, maintain, build muscle or reshape"),
    ("general", "Nutrition optimization", "Eat in a way that fits your body and routine"),
    ("gut_health", "Digestive and gut health", "Bloating, regularity and comfort"),
    ("healthy_aging", "Personal longevity plan", "Habits for healthy aging"),
    ("other", "Something else", "Tell me in your own words"),
]
WEIGHT_GOALS = ("lose_weight", "build_muscle", "maintain", "body_shape")  # all shown as the "weight" row
GOAL_LIST_BUTTON = "Set up a goal"
GOALS_TEXT = "Which goal would you like to set up? Your answers so far are saved – type HUB anytime to go back."


RESUME_TEXT = "Let's continue my {label} assessment [goal={goal}]"  # resume:<goal> as the agent sees it


def resume_buttons(a) -> list[Button]:
    return [Button(f"resume:{a.goal}", "Continue"), Button("menu:goals", "Set up a goal")]


def _goal_label(goal: str) -> str:
    return (goal_spec(goal) or {}).get("label", goal.replace("_", " "))


async def send_menu(db: AsyncSession, user: User) -> None:
    def here(mode: str) -> str:
        return "You're here · " if user.active_mode == mode else ""

    a = await open_assessment(db, user)
    if a:
        rows = [ListRow(f"resume:{a.goal}", "Continue My Assessment",
                        f"Resume your personalized health assessment ({_goal_label(a.goal)})"),
                ListRow("menu:goals", "Set Up a Goal", "See all health goals – your answers are kept")]
    else:
        rows = [ListRow("menu:goals", "Set Up a Goal", here("companion") + "Choose a health goal and get a "
                        "personalized assessment")]
    # Without a clinic link, My Care asks which clinic first (enter_post_treatment) – the title doesn't name Essence
    rows += [
        ListRow("menu:plan", "My Longevity Plan", "View your personalized health goals and next steps"),
        ListRow("mode:post_treatment", "My Care & Follow-ups",
                here("post_treatment") + "Treatment support, check-ins and ongoing care"),
        ListRow("menu:services", "Explore Health Services", "Discover relevant providers and services"),
        ListRow("menu:human", "Connect with a Care Team", "Request support from a real person"),
        ListRow("menu:privacy", "Privacy & Settings", "Manage your data and preferences"),
    ]
    await outbound.send_list(db, user, "What would you like to do? You can type HUB anytime to come back here.",
                             "Your Longevity Hub", rows)


async def goal_status(db: AsyncSession, user: User) -> dict[str, str]:
    """Row code -> "plan" | "in_progress", from the user's assessments (abandoned and stale ones don't count)."""
    rows = await db.scalars(select(CompanionAssessment).where(CompanionAssessment.user_id == user.id))
    out: dict[str, str] = {}
    for a in rows:
        code = "weight" if a.goal in WEIGHT_GOALS else a.goal
        if a.status == "plan_active":
            out[code] = "plan"
        elif a.status in ("in_progress", "paused") and utcnow() - a.updated_at <= memory.ABANDON_AFTER:
            out.setdefault(code, "in_progress")
    return out


async def send_goal_list(db: AsyncSession, user: User, text: str = FIRST_GOALS_TEXT) -> None:
    """The goal list, each row marked with its status so the user sees what's done and what's waiting."""
    status = await goal_status(db, user)
    labels = {"plan": "✅ Plan ready · tap to view or update", "in_progress": "⏸ In progress · tap to continue"}
    await outbound.send_list(db, user, text, GOAL_LIST_BUTTON,
                             [ListRow(f"goal:{code}", title, labels.get(status.get(code), blurb))
                              for code, title, blurb in GOAL_ROWS])


async def enter_companion(db: AsyncSession, user: User) -> None:
    user.active_mode, user.mode_chosen_at = "companion", utcnow()
    a = await open_assessment(db, user)
    if a:
        await outbound.send_buttons(db, user, f"Welcome back to your health companion 🌿 Last time we were working "
                                    f"on *{_goal_label(a.goal)}*. Want to pick up where we left off, or set up a "
                                    "different goal? Your answers so far are kept either way.", resume_buttons(a))
        return
    if await memory.goals(db, user):
        await outbound.send_text(db, user, "Welcome back to your health companion 🌿 What's on your mind today?",
                                 sender="aster_system")
    else:
        await send_goal_list(db, user)


async def enter_post_treatment(db: AsyncSession, user: User) -> None:
    if "essence" not in await provider_links(db, user):
        # Post-treatment support needs a clinic; don't switch until we know which one.
        await outbound.send_buttons(
            db, user, "Treatment support is for clients of our partner clinics – right now that's Essence Aesthetics "
            "& Wellness. Were you treated at Essence?",
            [Button("entry:essence", "Yes, Essence client"), Button("mode:companion", "No, set health goals")],
        )
        return
    user.active_mode, user.mode_chosen_at = "post_treatment", utcnow()
    await memory.pause_open_assessment(db, user)
    journey = await journeys.get_primary_journey(db, user.id)
    if journey:
        text = (f"Your last recorded treatment is *{journey.treatment_label_raw}* on {journey.treatment_date:%b %-d} "
                f"(Day {journeys.day_post_treatment(user, journey)}). Tell me how you're feeling, or ask me anything "
                "about your aftercare.")
    else:
        text = "Which treatment did you have at Essence, and when? For example: \"Botox last Monday\"."
    await outbound.send_text(db, user, text, sender="aster_system")


def format_plan(plan: Plan) -> str:
    """The plan exactly as stored – what the user sees is what Aster remembers."""
    c = plan.content
    parts = [f"📋 *Your plan – {_goal_label(plan.goal)}* (version {plan.version}, {plan.created_at:%b %-d})",
             c.get("situation_summary", "")]
    for title, key in (("Priorities", "priorities"), ("Every day", "daily_habits"), ("Every week", "weekly_habits"),
                       ("What to track", "metrics")):
        if c.get(key):
            parts.append(f"*{title}*\n" + "\n".join(f"• {i}" for i in c[key]))
    if plan.confidence == "preliminary":
        missing = [x.split(": ", 1)[1] for x in c.get("limitations", []) if x.startswith("Not known yet")]
        parts.append("_This is a first version based on what I know so far"
                     + (f" (still unknown: {', '.join(missing)})" if missing else "")
                     + ". It gets better as you tell me more._")
    if plan.review_after:
        parts.append(f"_Let's look at it again around {plan.review_after:%b %-d}. Type HUB → My Longevity Plan to see it anytime._")
    return "\n\n".join(p for p in parts if p)


async def send_plan(db: AsyncSession, user: User) -> None:
    plan = await memory.active_plan(db, user)
    if not plan:
        await outbound.send_buttons(db, user, "You don't have a plan yet. In your health companion we can build one "
                                    "together – it starts with a few questions, and you can skip any of them.",
                                    [Button("mode:companion", "Start now"), Button("menu:open", "Hub")])
        return
    await outbound.send_text(db, user, format_plan(plan), sender="aster_system")


def team_for(user: User, journey=None) -> str:
    """Who gets a handoff: the clinic for treatment matters (or a treatment in the last 30 days), else Aster's team."""
    if user.active_mode == "post_treatment":
        return "essence"
    if journey is not None and journey.status == "active" and journeys.day_post_treatment(user, journey) <= 30:
        return "essence"
    return "aster"


async def request_person(db: AsyncSession, user: User) -> None:
    team = team_for(user)
    await create_handoff(db, user, reason="user_request", urgency="routine", note="User asked for a person.", team=team)
    if team == "essence":
        text = "Of course – I've asked the Essence team to reach out to you. You can also call the clinic at (317) 555-0142."
    else:
        text = "Of course – I've asked the Aster care team to get in touch with you here."
    await outbound.send_text(db, user, text, sender="aster_system")


async def handle(db: AsyncSession, user: User, button_id: str | None, text: str) -> None:
    """Handles an input for which is_router_input() is true. The user has already consented."""
    t = text.strip()
    action = button_id or SLASH.get(t.lower()) or ICE_BREAKERS.get(t.lower(), "menu:open")

    if action == "entry:essence":
        await add_link(db, user, "essence", "self_reported", "user_button")
        await outbound.send_buttons(db, user, "Thanks! Being an Essence client doesn't mean we only talk about "
                                    "treatments. What would you like to start with?", START_BUTTONS)
    elif action == "entry:direct":
        if user.entry_source in (None, "unknown", "skipped"):
            user.entry_source = "direct"
        await enter_companion(db, user)
    elif action == "entry:skip":
        user.entry_source = "skipped"
        await outbound.send_text(db, user, "No problem! Just tell me what's on your mind.", sender="aster_system")
        await send_menu(db, user)
    elif action == "mode:companion":
        await enter_companion(db, user)
    elif action == "menu:goals":
        # The goal list, even mid-assessment: picking a goal pauses the open one (its answers are kept)
        user.active_mode, user.mode_chosen_at = "companion", utcnow()
        first_time = not await memory.goals(db, user) and not await open_assessment(db, user)
        await send_goal_list(db, user, FIRST_GOALS_TEXT if first_time else GOALS_TEXT)
    elif action == "mode:post_treatment":
        await enter_post_treatment(db, user)
    elif action == "menu:plan":
        await send_plan(db, user)
    elif action == "menu:services":
        await referral.log(db, user, referral.ANY_NEED, "accepted", mode=user.active_mode)  # the user asked
        await outbound.send_text(db, user, "Sure. What kind of help are you looking for – for example a dietitian, a "
                                 "fitness coach, or help after a treatment? I'll share some neutral advice first, and "
                                 "options if they fit what you need.", sender="aster_system")
    elif action.startswith("ref:no:"):
        offer = await _offer(db, user, action.removeprefix("ref:no:"))
        if offer:
            offer.status, offer.decided_at = "declined", utcnow()
        await outbound.send_text(db, user, "No problem – I'm here whenever you need anything.", sender="aster_system")
    elif action.startswith("ref:share:"):
        slug, need = action.removeprefix("ref:share:").split(":", 1)
        await share_with_provider(db, user, slug, need)
    elif action == "ref:noshare":
        await outbound.send_text(db, user, "Okay – nothing was shared.", sender="aster_system")
    elif action == "menu:human":
        await request_person(db, user)
    elif action == "invite:no":
        await outbound.send_text(db, user, "No problem – it's in the menu whenever you'd like it.", sender="aster_system")
    elif action == "menu:privacy":
        await outbound.send_text(db, user, PRIVACY_TEXT.format(url=get_settings().privacy_policy_url),
                                 sender="aster_system")
    else:
        await send_menu(db, user)


async def _offer(db: AsyncSession, user: User, offer_id: str) -> ReferralOffer | None:
    try:
        offer = await db.get(ReferralOffer, uuid.UUID(offer_id))
    except ValueError:
        return None
    return offer if offer and offer.user_id == user.id else None


async def accept_offer(db: AsyncSession, user: User, offer_id: str) -> str | None:
    """The user tapped "Yes, show options": returns the need, or None if the offer isn't theirs."""
    offer = await _offer(db, user, offer_id)
    if not offer:
        return None
    offer.status, offer.decided_at = "accepted", utcnow()
    return offer.need


async def share_with_provider(db: AsyncSession, user: User, slug: str, need: str) -> None:
    """Sends the provider exactly the summary the user just saw – nothing else (no journey, no chat history).
    Kept apart from clinical handoffs so it never merges into one."""
    p = providers.get(slug)
    if not p:
        return
    summary = await referral.share_summary(db, user, need)
    text = f"📨 Referral from Aster · {'TEST USER' if user.is_test_user else ''}\nTo: {p['name']}\n{summary}\n" \
           f"Contact: +{user.wa_id}\n(Shared with the user's permission.)"
    db.add(Handoff(user_id=user.id, reason="referral_share", urgency="routine", status="sent", summary=text,
                   team=slug, notified=True))
    await referral.log(db, user, need, "contact_shared", shown=[slug], mode=user.active_mode)
    await outbound.notify_staff(text)
    c = p["contact"]
    how = " · ".join(v for v in (c.get("phone"), c.get("email"), c.get("booking_url") or c.get("website")) if v)
    await outbound.send_text(db, user, f"Done – I've sent {p['name']} your summary, and they'll get in touch. You can "
                             f"also reach them directly: {how}", sender="aster_system")


COMPANION_INVITE = ("One more thing: besides your recovery, I can also help with your own health goals – nutrition, "
                    "sleep, energy, movement – whenever you like. Want to take a look?")
INVITE_BUTTONS = [Button("mode:companion", "Sure, show me"), Button("invite:no", "Not now")]


async def welcome_back(db: AsyncSession, user: User) -> None:
    """A returning user who just says hi: offer to continue rather than starting over."""
    name = user.first_name or "there"
    a = await open_assessment(db, user)
    if a:
        await outbound.send_buttons(
            db, user, f"Welcome back, {name}! 💛 Last time we were working on *{_goal_label(a.goal)}*. Want to pick "
            "up where we left off?", [*resume_buttons(a), Button("menu:open", "Hub")])
    else:
        await outbound.send_text(db, user, f"Welcome back, {name}! 💛", sender="aster_system")
        await send_menu(db, user)
