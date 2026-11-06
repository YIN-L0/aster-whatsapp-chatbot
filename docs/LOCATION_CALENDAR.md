# Location, nearby options and calendar

Client need 3 ("Calendar and Location Access"), adapted to what WhatsApp allows: a bot cannot read or write a
user's calendar, and it only gets a location when the user shares one.

## Decisions (2026-11-06)

| Topic | Decision |
|---|---|
| Calendar | One-tap links: an `.ics` file (iPhone, Outlook, Apple Calendar) and a Google Calendar link. No Google / Microsoft login. Aster sends its own reminders in the chat. |
| Nearby search | A Google Maps search link ("registered dietitian near Carmel, IN 46032"). No API key, no cost. Aster doesn't list, rank or vouch for non-partner providers. |
| Display | Partner options from `find_services` first; the nearby link comes after them, labelled "not Aster partners". |
| Location privacy | Only ZIP / city / state is stored. A shared pin is turned into an area on arrival (OpenStreetMap reverse geocoding) and the coordinates are dropped. Users can also type a ZIP or city. |

## How it works

**Location**
- `search_nearby` without a known area sets a "Send location" request (WhatsApp `location_request_message`; in web
  chat, the browser's location). The user can also type a ZIP code or city → `set_location`.
- A shared pin arrives as a `location` message. `pipeline.ingest` calls `places.reverse()`, saves
  `users.location_zip / location_city / location_state`, and stores only `📍 Shared my location: Carmel, IN 46032` as the
  message body. Before consent, nothing is looked up or stored.
- The agent sees `location=` in the context. `#reset` clears it; the privacy text says what is kept.

**Nearby options**
- `search_nearby(query)` → the system sends, after the agent's reply, the Maps link with the "not reviewed by Aster"
  note. Logged as `referral_offers.status = nearby_shown`.
- Prompt rule: only when the user wants options near them beyond `find_services`, or none fit; never for emergencies.

**Calendar**
- When a user mentions an appointment outside Aster, the agent asks for what's missing (date, time, place), offers to
  add it, and calls `add_to_calendar` only after a yes.
- The appointment is saved (`source=user_reported`, `provider_slug=external`) with its place and duration. The system
  sends the `.ics` link (`/cal/<appointment id>.ics`, a random UUID) and the Google Calendar link.
- Reminders (if check-ins are on): the day before, plus 2 hours before.

## Settings
- `PUBLIC_BASE_URL`: the server's public address for the `.ics` links. When empty, Render's `RENDER_EXTERNAL_URL` or
  Railway's `RAILWAY_PUBLIC_DOMAIN` is used, else `http://localhost:8000`.
- `GEOCODER_URL`: defaults to OpenStreetMap Nominatim (free, about 1 request a second, needs an identifying
  User-Agent). Replace it with a paid geocoder before high volume.

## Known limits
- Reminders more than 24 hours after the user's last message need an approved WhatsApp template (this is the same
  for Essence appointment reminders today). Until then, those reminders fail and are marked `failed`.
- Nominatim gets the pin (not stored by Aster). Mention OpenStreetMap in the privacy policy before launch.
