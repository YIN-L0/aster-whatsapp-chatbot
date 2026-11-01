"""Provider catalog and service matching (docs/COMPANION_REDESIGN.md §6.4) – pure functions, no DB and no LLM.

Each provider is one YAML file in app/provider_catalog/. Matching is neutral: providers are ranked by how well they fit the
need and the user's constraints. A provider the user already has a link with (e.g. Essence for an Essence client)
only wins a tie – it never pushes a better-fitting option down.
"""

from functools import lru_cache
from pathlib import Path

import yaml

from app.assessment_engine import CAUTION_BLOCKED_SERVICES

PROVIDERS_DIR = Path(__file__).parent / "provider_catalog"
MAX_OPTIONS = 3

# Needs Aster can match. The first block is weight_gut.yaml's generic services, with neutral labels.
NEEDS = {
    "s_dietitian": "Registered dietitian",
    "s_dna_coaching": "Nutrition coaching",
    "s_dna_test": "DNA nutrition test",
    "s_dna_upload": "Report from existing DNA data",
    "s_programs": "Structured goal program",
    "s_glp1_program": "Support while on GLP-1 medication",
    "s_fitness": "Fitness coaching",
    "s_sports_nutrition": "Sports nutrition",
    "s_gut_eval": "Digestive health evaluation",
    "s_labs": "Lab tests",
    "s_nutrient_labs": "Nutrient blood tests",
    "s_genetic_counsel": "Genetic counseling",
    "s_medspa": "Body contouring",
    "s_medical_weight": "Medical weight management",
    "s_skin_care": "Skin treatments",
    "s_injectables": "Injectables",
    "s_hair": "Hair restoration",
    "s_iv_therapy": "IV and vitamin injections",
    "s_hormone_health": "Hormone and wellness consultation",
    "s_primary_care": "Primary care",
}


@lru_cache
def catalog() -> dict[str, dict]:
    out = {}
    for f in sorted(PROVIDERS_DIR.glob("*.yaml")):
        if f.stem != "neutral":
            p = yaml.safe_load(f.read_text(encoding="utf-8"))
            out[p["slug"]] = p
    return out


@lru_cache
def neutral_options() -> dict[str, str]:
    return yaml.safe_load((PROVIDERS_DIR / "neutral.yaml").read_text(encoding="utf-8"))


def get(slug: str | None) -> dict | None:
    return catalog().get(slug) if slug else None


def banned_plan_terms() -> list[str]:
    """Names that must never appear in a personal plan."""
    return [t for p in catalog().values() for t in [p["name"], *p.get("aliases", [])]]


def _in_area(provider: dict, state: str | None) -> bool | None:
    """True / False when we can tell, None when the user's state is unknown."""
    if not state:
        return None
    return any(loc.get("country") == "US" or loc.get("state", "").upper() == state.upper()
               for loc in provider.get("locations", []))


def match(need: str, *, state: str | None = None, mode: str | None = None, age: float | None = None,
          caution: bool = False, linked: frozenset[str] | set[str] = frozenset(), max_n: int = MAX_OPTIONS) -> dict:
    """Ranked options for one need.

    state: user's US state (e.g. "IN") if known · mode: "virtual" | "in_person" if the user has a preference ·
    caution: minor, pregnancy or a nutrition concern (no weight-loss style services) ·
    linked: provider slugs the user already has a relationship with (tie-break only).
    """
    if need not in NEEDS:
        return {"error": "unknown_need", "needs": sorted(NEEDS)}
    options, excluded = [], []
    for p in catalog().values():
        for svc in p["services"]:
            if svc["need"] != need:
                continue
            modes = svc.get("modes", p["modes"])
            area = _in_area(p, state)
            why = None
            if caution and need in CAUTION_BLOCKED_SERVICES:
                why = "not suitable in this situation"
            elif age is not None and age < p.get("age_min", 0):
                why = f"only for ages {p['age_min']}+"
            elif mode and mode not in modes:
                why = f"no {mode.replace('_', '-')} option"
            elif "virtual" not in modes and area is False:
                why = "not in your area"
            if why:
                excluded.append({"provider": p["slug"], "why": why})
                continue
            reasons = [f"Offers {svc['label'].lower()}"]
            uncertainties = []
            score = 2
            if "virtual" in modes:
                reasons.append("Online, so location doesn't matter")
                score += 1
            elif area:
                loc = p["locations"][0]
                reasons.append(f"In person in {loc.get('city')}, {loc.get('state')}")
                score += 1
            else:
                loc = p["locations"][0]
                uncertainties.append(f"In person only, in {loc.get('city')}, {loc.get('state')} – check that works for you")
            if mode and mode in modes:
                score += 1
            if svc.get("consult_first"):
                uncertainties.append("Starts with a consultation to see if it suits you")
            if svc.get("note"):
                uncertainties.append(svc["note"])
            if p["slug"] in linked:
                reasons.append("You're already a client here, so your care stays in one place")
            options.append({
                "provider": p["slug"], "name": p["name"], "service": svc["label"], "about": svc.get("about"),
                "price_text": svc.get("price_text"), "reasons": reasons, "uncertainties": uncertainties,
                "disclosure": p["relationship"]["disclosure"], "contact": p["contact"], "booking": p["booking"],
                "existing_relationship": p["slug"] in linked, "_score": score,
            })
    options.sort(key=lambda o: (-o["_score"], not o["existing_relationship"], o["name"]))
    for o in options:
        del o["_score"]
    return {"need": need, "need_label": NEEDS[need], "options": options[:max_n],
            "neutral": neutral_options().get(need), "excluded": excluded}
