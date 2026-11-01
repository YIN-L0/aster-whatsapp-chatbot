"""Entry, menu and mode routing (docs/COMPANION_REDESIGN.md §5–6). Deterministic: no LLM call.

WhatsApp has no persistent menu, so the menu is summoned: MENU / slash commands send a list message, and tapping a
row switches active_mode. Switching never touches progress – assessments, plans and journeys live in their own tables.
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import journeys, outbound, providers
from app.channels import Button, ListRow
from app.companion import goal_spec
from app.config import get_settings
from app.handoff import create_handoff
from app.models import CompanionAssessment, Message, Plan, ProviderLink, User

PREFIXES = ("entry:", "menu:", "mode:", "invite:")
MENU_WORDS = {"MENU", "HOME", "SWITCH", "/MENU"}
SLASH = {"/companion": "mode:companion", "/aftercare": "mode:post_treatment", "/treatment": "mode:post_treatment",
         "/plan": "menu:plan", "/services": "menu:services", "/human": "menu:human", "/privacy": "menu:privacy"}
# WhatsApp ice breakers (shown on an empty chat) arrive as plain text: route them like the matching button.
# Keep in sync with scripts/setup_whatsapp_commands.py. "What is Aster?" is left to the agent.
ICE_BREAKERS = {"help me with my health goals": "mode:companion", "questions after my treatment": "mode:post_treatment"}
GREETING = re.compile(r"^\s*(hi+|hello|hey|hiya|yo|good (morning|afternoon|evening))( aster)?[\s!.,💛👋🙂]*$", re.I)

TRIAGE_TEXT = ("One quick question so I can help in the right way: have you had care or treatment at "
               "*Essence Aesthetics & Wellness* before?")
TRIAGE_BUTTONS = [Button("entry:essence", "Yes, Essence client"), Button("entry:direct", "No, my health goals"),
                  Button("entry:skip", "Skip for now")]
START_BUTTONS = [Button("mode:post_treatment", "My treatment"), Button("mode:companion", "My health goals")]
COMPANION_INTRO = (
    "🌿 *Your health companion*\n\n"
    "This is your space for your own health goals – nutrition, weight, digestion, movement, sleep, stress, whatever "
    "matters to you. I'll listen, give you honest, science-based feedback, and we can build a personal plan together "
    "over time. No sales pitch: if you ever want a professional or a service, just ask.\n\n"
    "What would you most like to work on right now?"
)
MENU_HINT = "_Type MENU anytime to switch between your health goals and treatment support._"
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
    return t.upper() in MENU_WORDS or t.lower() in SLASH or t.lower() in ICE_BREAKERS


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


async def after_consent(db: AsyncSession, user: User) -> None:
    """The first step after I Agree, by where the user came from. An Essence client still chooses what to talk about."""
    if user.entry_source == "essence":
        await outbound.send_buttons(db, user, "Welcome from Essence! What would you like to start with? I can help "
                                    "with questions after a treatment, or with your own health goals.", START_BUTTONS)
    elif providers.get(user.entry_source):
        user.active_mode = "companion"
        name = providers.get(user.entry_source)["name"]
        await outbound.send_text(db, user, f"Welcome from {name}! 🌿\n\n{COMPANION_INTRO}\n\n{MENU_HINT}",
                                 sender="aster_system")
    else:
        await outbound.send_buttons(db, user, TRIAGE_TEXT, TRIAGE_BUTTONS)


# ---------- menu and modes ----------


async def open_assessment(db: AsyncSession, user: User) -> CompanionAssessment | None:
    return await db.scalar(
        select(CompanionAssessment)
        .where(CompanionAssessment.user_id == user.id, CompanionAssessment.status.in_(("in_progress", "paused")))
        .order_by(CompanionAssessment.created_at.desc())
    )


async def active_plan(db: AsyncSession, user: User) -> Plan | None:
    return await db.scalar(select(Plan).where(Plan.user_id == user.id, Plan.status == "active")
                           .order_by(Plan.version.desc()))


def _goal_label(goal: str) -> str:
    return (goal_spec(goal) or {}).get("label", goal.replace("_", " "))


async def send_menu(db: AsyncSession, user: User) -> None:
    here = "You're here · "
    a = await open_assessment(db, user)
    if a:
        rows = [ListRow("mode:companion", "Continue my assessment", f"{_goal_label(a.goal)} – pick up where we left off")]
    else:
        rows = [ListRow("mode:companion", "Health companion", (here if user.active_mode == "companion" else "")
                        + "Your goals, nutrition, habits and plan")]
    if "essence" in await provider_links(db, user):
        rows.append(ListRow("mode:post_treatment", "Treatment care & visits",
                            (here if user.active_mode == "post_treatment" else "")
                            + "Aftercare questions, check-ins and Essence visits"))
    else:
        rows.append(ListRow("entry:essence", "I'm an Essence client", "Support after a treatment at Essence"))
    rows += [
        ListRow("menu:plan", "My plan", "See your current health plan"),
        ListRow("menu:services", "Find a service", "Professionals and partner services, when you want them"),
        ListRow("menu:human", "Talk to a person", "Ask the team to get in touch"),
        ListRow("menu:privacy", "Privacy & settings", "Messages, your data and how to stop"),
    ]
    await outbound.send_list(db, user, "What would you like to do? You can type MENU anytime to come back here.",
                             "Open menu", rows)


async def enter_companion(db: AsyncSession, user: User) -> None:
    user.active_mode = "companion"
    a = await open_assessment(db, user)
    if a:
        text = (f"Welcome back to your health companion 🌿 Last time we were working on *{_goal_label(a.goal)}*. "
                "Want to pick up where we left off, or is something else on your mind?")
    else:
        text = COMPANION_INTRO
    await outbound.send_text(db, user, text, sender="aster_system")


async def enter_post_treatment(db: AsyncSession, user: User) -> None:
    if "essence" not in await provider_links(db, user):
        # Post-treatment support needs a clinic; don't switch until we know which one.
        await outbound.send_buttons(
            db, user, "Treatment support is for clients of our partner clinics – right now that's Essence Aesthetics "
            "& Wellness. Were you treated at Essence?",
            [Button("entry:essence", "Yes, Essence client"), Button("mode:companion", "No, my health goals")],
        )
        return
    user.active_mode = "post_treatment"
    journey = await journeys.get_primary_journey(db, user.id)
    if journey:
        text = (f"Your last recorded treatment is *{journey.treatment_label_raw}* on {journey.treatment_date:%b %-d} "
                f"(Day {journeys.day_post_treatment(user, journey)}). Tell me how you're feeling, or ask me anything "
                "about your aftercare.")
    else:
        text = "Which treatment did you have at Essence, and when? For example: \"Botox last Monday\"."
    await outbound.send_text(db, user, text, sender="aster_system")


async def send_plan(db: AsyncSession, user: User) -> None:
    plan = await active_plan(db, user)
    if not plan:
        await outbound.send_buttons(db, user, "You don't have a plan yet. In your health companion we can build one "
                                    "together – it starts with a few questions, and you can skip any of them.",
                                    [Button("mode:companion", "Start now"), Button("menu:open", "Menu")])
        return
    c = plan.content
    parts = [f"*Your plan – {_goal_label(plan.goal)}* (version {plan.version}, {plan.created_at:%b %-d})",
             c.get("situation_summary", "")]
    for title, key in (("Priorities", "priorities"), ("Every day", "daily_habits"), ("Every week", "weekly_habits"),
                       ("What to track", "metrics")):
        if c.get(key):
            parts.append(f"*{title}*\n" + "\n".join(f"• {i}" for i in c[key]))
    if plan.confidence == "preliminary":
        parts.append("_This is a first version – it gets better as you tell me more._")
    await outbound.send_text(db, user, "\n\n".join(p for p in parts if p), sender="aster_system")


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
    elif action == "mode:post_treatment":
        await enter_post_treatment(db, user)
    elif action == "menu:plan":
        await send_plan(db, user)
    elif action == "menu:services":
        await outbound.send_text(db, user, "Sure. What kind of help are you looking for – for example a dietitian, a "
                                 "fitness coach, or help after a treatment? I'll share some neutral advice first, and "
                                 "options if they fit what you need.", sender="aster_system")
    elif action == "menu:human":
        await request_person(db, user)
    elif action == "invite:no":
        await outbound.send_text(db, user, "No problem – it's in the menu whenever you'd like it.", sender="aster_system")
    elif action == "menu:privacy":
        await outbound.send_text(db, user, PRIVACY_TEXT.format(url=get_settings().privacy_policy_url),
                                 sender="aster_system")
    else:
        await send_menu(db, user)


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
            "up where we left off?", [Button("mode:companion", "Continue"), Button("menu:open", "Something else")])
    else:
        await outbound.send_text(db, user, f"Welcome back, {name}! 💛", sender="aster_system")
        await send_menu(db, user)
