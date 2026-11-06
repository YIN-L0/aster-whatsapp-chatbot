"""Configure WhatsApp's conversational components for the Aster number (docs/COMPANION_REDESIGN.md §5):
slash commands (shown when the user types "/"), ice breakers (shown on an empty chat) and the welcome event.

    .venv/bin/python -m scripts.setup_whatsapp_commands            # print the request only
    .venv/bin/python -m scripts.setup_whatsapp_commands --apply    # send it to the Graph API
    .venv/bin/python -m scripts.setup_whatsapp_commands --show     # read the current settings

Commands arrive as normal text ("/hub") and are handled by app.router. Ice breaker texts must match
app.router.ICE_BREAKERS (except "What is Aster?", which the agent answers).
"""

import argparse
import json

import httpx

from app.config import get_settings

SETTINGS = {
    "enable_welcome_message": True,
    "commands": [
        {"command_name": "hub", "command_description": "Open Your Longevity Hub"},
        {"command_name": "goals", "command_description": "Set up a health goal"},
        {"command_name": "companion", "command_description": "Your health goals and personalized assessment"},
        {"command_name": "aftercare", "command_description": "My care & follow-ups: treatment support and check-ins"},
        {"command_name": "plan", "command_description": "View your longevity plan"},
        {"command_name": "human", "command_description": "Connect with a care team"},
        {"command_name": "privacy", "command_description": "Manage your data and preferences"},
    ],
    # Up to 4, max 80 characters, no emoji
    "prompts": ["Help me with my health goals", "Questions after my treatment", "What is Aster?"],
}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="send the settings to the Graph API")
    p.add_argument("--show", action="store_true", help="print the number's current settings")
    args = p.parse_args()
    s = get_settings()
    base = f"https://graph.facebook.com/{s.wa_graph_version}/{s.wa_phone_number_id}"
    headers = {"Authorization": f"Bearer {s.wa_access_token}"}
    if args.show:
        r = httpx.get(base, params={"fields": "conversational_automation"}, headers=headers, timeout=15)
        print(r.status_code, json.dumps(r.json(), indent=2))
        return
    print(json.dumps(SETTINGS, indent=2))
    if not args.apply:
        print("\nDry run – add --apply to send.")
        return
    if not s.whatsapp_enabled:
        raise SystemExit("WA_ACCESS_TOKEN and WA_PHONE_NUMBER_ID must be set.")
    r = httpx.post(f"{base}/conversational_automation", json=SETTINGS, headers=headers, timeout=15)
    print(r.status_code, r.text[:500])


if __name__ == "__main__":
    main()
