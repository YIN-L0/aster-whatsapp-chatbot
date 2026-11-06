"""Where the user is (ZIP / city / state only) and nearby-search links. No API key and no cost.

Privacy: a shared location pin is turned into a ZIP code and city right away (OpenStreetMap reverse geocoding) and the
pin itself is dropped – coordinates are never stored. Users can also just type a ZIP code or city.
Nearby results are a Google Maps search link: Aster doesn't list, rank or vouch for non-partner providers itself.
"""

import logging
import os
import re
from urllib.parse import quote_plus

import httpx

from app.config import get_settings
from app.models import User

log = logging.getLogger(__name__)

ZIP_RE = re.compile(r"^\d{5}$")
STATE_RE = re.compile(r"^[A-Z]{2}$")
_transport: httpx.AsyncBaseTransport | None = None  # tests replace the geocoder


def label(user: User) -> str | None:
    """'Carmel, IN 46032', 'Carmel, IN', '46032' – or None when unknown."""
    place = ", ".join(x for x in (user.location_city, user.location_state) if x)
    return " ".join(x for x in (place, user.location_zip) if x) or None


def set_area(user: User, *, zip_code: str | None = None, city: str | None = None, state: str | None = None) -> list[str]:
    """Validates and saves; returns problems (nothing is saved when there are any)."""
    zip_code = (zip_code or "").strip()[:5] or None
    city = " ".join((city or "").split())[:80] or None
    state = (state or "").strip().upper() or None
    problems = []
    if zip_code and not ZIP_RE.match(zip_code):
        problems.append("zip must be 5 digits")
    if state and not STATE_RE.match(state):
        problems.append("state must be 2 letters, e.g. IN")
    if not (zip_code or city):
        problems.append("need a ZIP code or a city")
    if problems:
        return problems
    user.location_zip, user.location_city, user.location_state = zip_code, city, state
    return []


async def reverse(lat: float, lon: float) -> dict | None:
    """{zip, city, state} for a pin, or None. Only the area leaves this function."""
    try:
        async with httpx.AsyncClient(transport=_transport, timeout=4.0,
                                     headers={"User-Agent": "AsterHealthCompanion/1.0"}) as client:
            r = await client.get(get_settings().geocoder_url,
                                 params={"format": "jsonv2", "lat": f"{lat:.5f}", "lon": f"{lon:.5f}", "zoom": 14,
                                         "addressdetails": 1})
            r.raise_for_status()
            a = r.json().get("address") or {}
    except Exception as e:  # network, timeout, bad JSON – the user can still type a ZIP code
        log.warning("reverse geocoding failed: %s", e)
        return None
    zip_code = (a.get("postcode") or "").split("-")[0].strip()
    city = a.get("city") or a.get("town") or a.get("village") or a.get("hamlet") or a.get("county")
    iso = a.get("ISO3166-2-lvl4") or ""
    state = iso.split("-", 1)[1] if iso.startswith("US-") else None
    out = {"zip": zip_code if ZIP_RE.match(zip_code) else None, "city": city, "state": state}
    return out if out["zip"] or out["city"] else None


def maps_url(query: str, user: User) -> str:
    q = f"{' '.join(query.split())[:60]} near {label(user)}"
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(q)}"


def nearby_message(query: str, user: User) -> str:
    return (f"📍 *Other options near {label(user)}* – not Aster partners\n{maps_url(query, user)}\n\n"
            "_These are Google Maps results that Aster hasn't reviewed. Check reviews, credentials and insurance "
            "before you book._")


def public_base_url() -> str:
    s = get_settings()
    if s.public_base_url:
        return s.public_base_url.rstrip("/")
    if os.environ.get("RENDER_EXTERNAL_URL"):
        return os.environ["RENDER_EXTERNAL_URL"].rstrip("/")
    if os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
        return "https://" + os.environ["RAILWAY_PUBLIC_DOMAIN"]
    return "http://localhost:8000"
