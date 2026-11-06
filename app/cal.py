"""Calendar links for an appointment: an .ics file (iPhone, Outlook, Apple Calendar) and a Google Calendar link.

WhatsApp bots can't get access to a user's calendar, so Aster sends links the user taps once to save the event.
The .ics file is served at /cal/<appointment id>.ics (the id is a random UUID).
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from app.models import Appointment, User
from app.places import public_base_url

DEFAULT_MINUTES = 60


def _utc(dt: datetime) -> datetime:
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _span(appt: Appointment) -> tuple[datetime, datetime]:
    start = _utc(appt.starts_at)
    return start, start + timedelta(minutes=appt.duration_minutes or DEFAULT_MINUTES)


def title(appt: Appointment) -> str:
    what = (appt.service or "Appointment").strip()
    return f"{what} – {appt.provider_name}" if appt.provider_name else what


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics(appt: Appointment) -> str:
    start, end = _span(appt)
    f = "%Y%m%dT%H%M%SZ"
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Aster//Health Companion//EN", "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT", f"UID:{appt.id}@aster", f"DTSTAMP:{datetime.now(timezone.utc):{f}}",
        f"DTSTART:{start:{f}}", f"DTEND:{end:{f}}", f"SUMMARY:{_esc(title(appt))}",
        *([f"LOCATION:{_esc(appt.location)}"] if appt.location else []),
        "DESCRIPTION:Added by Aster",
        "BEGIN:VALARM", "TRIGGER:-PT1H", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(title(appt))}", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


def ics_url(appt: Appointment) -> str:
    return f"{public_base_url()}/cal/{appt.id}.ics"


def google_url(appt: Appointment) -> str:
    start, end = _span(appt)
    f = "%Y%m%dT%H%M%SZ"
    q = {"action": "TEMPLATE", "text": title(appt), "dates": f"{start:{f}}/{end:{f}}", "details": "Added by Aster"}
    if appt.location:
        q["location"] = appt.location
    return "https://calendar.google.com/calendar/render?" + urlencode(q)


def message(appt: Appointment, user: User, reminders: bool) -> str:
    when = _utc(appt.starts_at).astimezone(ZoneInfo(user.timezone))
    text = (f"📅 *{title(appt)}*\n{when:%a, %b %-d at %-I:%M %p}" + (f" · {appt.location}" if appt.location else "")
            + f"\n\nTap to add it to your calendar:\n• iPhone / Outlook: {ics_url(appt)}\n• Google Calendar: "
            f"{google_url(appt)}")
    if reminders:
        text += "\n\nI'll also remind you here the day before and 2 hours before."
    return text
