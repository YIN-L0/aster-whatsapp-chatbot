"""Companion assessment – pure functions over app/assessment_rules/companion.yaml (docs/COMPANION_REDESIGN.md §6.3).

No DB and no LLM. Code decides which slot to ask next, whether a plan can be made and whether a drafted plan is
acceptable; the LLM phrases questions and drafts plan text. Weight, nutrition and gut values are checked against
the weight_gut tables and evaluated by app.assessment_engine.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import yaml

from app import assessment_engine as eng

RULES_FILE = Path(__file__).parent / "assessment_rules" / "companion.yaml"
SEXES = {"female", "male", "prefer_not"}
GLP1 = {"none", "effective", "side_effects"}
PORTIONS = {"small", "regular", "large"}
INTENSITIES = {"light", "moderate", "vigorous"}
# The engine's handoff reasons that stop a plan (the rest only make it more cautious)
BLOCKING = {"gi_red_flag"}


@dataclass
class Fact:
    value: dict
    recorded_at: datetime


@lru_cache
def rules() -> dict:
    return yaml.safe_load(RULES_FILE.read_text(encoding="utf-8"))


def goal_spec(goal: str | None) -> dict | None:
    return rules()["goals"].get(goal) if isinstance(goal, str) else None


def is_structured(goal: str | None) -> bool:
    spec = goal_spec(goal)
    return bool(spec and spec["structured"])


# ---------- slot values ----------


def _codes(value, allowed: set[str], problems: list[str], name: str) -> list[str]:
    if not isinstance(value, list):
        problems.append(f"{name}: expected a list of codes")
        return []
    good = [v for v in value if isinstance(v, str) and v in allowed]
    bad = [v for v in value if v not in good]
    if bad:
        problems.append(f"{name}: unknown codes {bad}")
    return list(dict.fromkeys(good))


def validate_slot(slot: str, value: dict) -> tuple[dict, list[str]]:
    """Cleans what the LLM extracted for a slot. Returns (clean fields, problems). Bad values are dropped, never
    guessed, and the problems tell the LLM what to ask again. Accepts lb and ft/in and converts them."""
    w = eng.rules()
    value = value if isinstance(value, dict) else {}
    clean: dict = {}
    problems: list[str] = []

    if slot == "body_measures":
        kg = eng._num(value.get("weight_kg"), 25, 350)
        if kg is None and eng._num(value.get("weight_lb"), 55, 770) is not None:
            kg = eng.lb_to_kg(float(value["weight_lb"]))
        cm = eng._num(value.get("height_cm"), 100, 250)
        if cm is None and eng._num(value.get("height_ft"), 3, 8) is not None:
            cm = eng.ft_in_to_cm(float(value["height_ft"]), eng._num(value.get("height_in"), 0, 11.99) or 0)
        if kg is not None:
            clean["weight_kg"] = kg
        if cm is not None:
            clean["height_cm"] = cm
        problems += [f"missing or implausible {k}" for k in ("weight_kg", "height_cm") if k not in clean]
    elif slot == "age":
        age = eng._num(value.get("age"), 1, 120)
        if age is not None:
            clean["age"] = int(age)
        elif eng.age_years(age_band=value.get("age_band")) is not None:
            clean["age_band"] = str(value["age_band"])[:16]
        else:
            problems.append("missing or implausible age")
    elif slot == "sex":
        if value.get("sex") in SEXES:
            clean["sex"] = value["sex"]
        else:
            problems.append("sex must be female, male or prefer_not")
    elif slot == "exercise":
        acts = value.get("activities")
        if not isinstance(acts, list):
            problems.append("activities: expected a list ([] means no regular exercise)")
        else:
            known_codes = set(w["exercise"]["activities"])
            clean["activities"] = []
            for a in acts:
                if not isinstance(a, dict):
                    continue
                item = {"code": a.get("code") if a.get("code") in known_codes else "other"}
                for k, lo, hi in (("sessions_per_week", 0, 21), ("minutes", 0, 600)):
                    if eng._num(a.get(k), lo, hi) is not None:
                        item[k] = float(a[k])
                if a.get("intensity") in INTENSITIES:
                    item["intensity"] = a["intensity"]
                clean["activities"].append(item)
            if any("sessions_per_week" not in a or "minutes" not in a for a in clean["activities"]):
                problems.append("some activities have no sessions_per_week or minutes – ask once, briefly")
    elif slot == "meals":
        meals = eng._num(value.get("meals_per_day"), 0, 12)
        if meals is not None:
            clean["meals_per_day"] = int(meals)
        if "diet_flags" in value:
            clean["diet_flags"] = _codes(value["diet_flags"], set(w["diet"]["flags"]), problems, "diet_flags")
        if isinstance(value.get("foods"), list):
            clean["foods"] = [
                {"item": str(f.get("item", ""))[:80],
                 "portion": f.get("portion") if f.get("portion") in PORTIONS else "regular",
                 "kcal_est": eng._num(f.get("kcal_est"), 0, 5000)}
                for f in value["foods"] if isinstance(f, dict) and f.get("item")
            ]
        if not (clean.get("meals_per_day") is not None or clean.get("diet_flags") or clean.get("foods")):
            problems.append("no usable meal information")
    elif slot == "diet_plan":
        if "diet_plans" in value:
            clean["diet_plans"] = _codes(value["diet_plans"], set(w["diet_plans"]), problems, "diet_plans")
        if "eating_patterns" in value:
            clean["eating_patterns"] = _codes(value["eating_patterns"], set(w["eating_patterns"]), problems,
                                              "eating_patterns")
        if value.get("glp1") in GLP1:
            clean["glp1"] = value["glp1"]
        if not any(clean.values()):
            problems.append("no usable diet plan information")
    elif slot == "gut_symptoms":
        allowed = set(w["gut"]["symptoms"]) | set(w["gut"]["red_flags"])
        clean["gi_symptoms"] = _codes(value.get("gi_symptoms"), allowed, problems, "gi_symptoms")
        if not clean["gi_symptoms"]:
            problems.append("no usable symptom codes (use gi_none for no symptoms)")
    else:
        problems.append(f"unknown slot {slot}")
    return clean, problems


def slot_complete(slot: str, clean: dict) -> bool:
    if slot == "body_measures":
        return "weight_kg" in clean and "height_cm" in clean
    if slot == "exercise":
        return "activities" in clean
    if slot in ("meals", "diet_plan"):
        return any(v not in (None, [], {}) for v in clean.values())
    if slot == "gut_symptoms":
        return bool(clean.get("gi_symptoms"))
    return bool(clean)


# ---------- progress ----------


def slot_state(slot: str, facts: dict[str, Fact], skipped: set[str], now: datetime) -> str:
    """known | stale | skipped | missing"""
    fact = facts.get(slot)
    if fact and slot_complete(slot, fact.value):
        ttl = rules()["slots"][slot]["ttl_days"]
        return "known" if not ttl or now - fact.recorded_at < timedelta(days=ttl) else "stale"
    return "skipped" if slot in skipped else "missing"


def next_slot(goal: str | None, facts: dict[str, Fact], skipped: set[str], now: datetime) -> dict | None:
    """The next slot to ask about (core first, in YAML order), or None when nothing is left.
    A stale answer is confirmed ("still about right?") instead of asked from scratch."""
    spec = goal_spec(goal)
    if not spec or not spec["structured"]:
        return None
    for slot in spec["core"] + spec["supporting"]:
        state = slot_state(slot, facts, skipped, now)
        if state in ("missing", "stale"):
            s = rules()["slots"][slot]
            return {"slot": slot, "mode": "confirm" if state == "stale" else "ask",
                    "previous": facts[slot].value if state == "stale" else None,
                    "purpose": s["purpose"], "ask_hint": s["ask_hint"], "examples": s["examples"]}
    return None


def engine_answers(goal: str | None, facts: dict[str, Fact]) -> dict:
    """Profile facts → the {step: {field: value}} shape app.assessment_engine.evaluate() expects."""
    answers: dict[str, dict] = {"q0": {"goal": goal if is_structured(goal) else None}}
    for slot, fact in facts.items():
        spec = rules()["slots"].get(slot)
        if spec:
            step = answers.setdefault(spec["step"], {})
            step.update({k: v for k, v in fact.value.items() if k in spec["fields"]})
    if answers.get("a1_q1", {}).get("sex") == "prefer_not":
        answers["a1_q1"]["sex"] = None
    return answers


def readiness(goal: str | None, facts: dict[str, Fact], skipped: set[str], now: datetime,
              result: dict | None = None) -> dict:
    """How far a plan can go for this goal (§6.3). Depends on what is known for the goal, not on a question count.
    Levels: chat_only · blocked · not_ready · preliminary · ready."""
    spec = goal_spec(goal)
    if not spec or not spec["structured"]:
        return {"level": "chat_only", "missing": [], "skipped": []}
    r = rules()["readiness"]
    state = {s: slot_state(s, facts, skipped, now) for s in spec["core"] + spec["supporting"]}
    core_known = [s for s in spec["core"] if state[s] == "known"]
    sup_known = [s for s in spec["supporting"] if state[s] == "known"]
    core_resolved = all(state[s] in ("known", "skipped") for s in spec["core"])
    out = {
        "core_known": len(core_known), "core_total": len(spec["core"]),
        "supporting_known": len(sup_known), "supporting_total": len(spec["supporting"]),
        "missing": [s for s, v in state.items() if v in ("missing", "stale")],
        "skipped": [s for s, v in state.items() if v == "skipped"],
    }
    result = result if result is not None else eng.evaluate(engine_answers(goal, facts))
    if (result.get("handoff") or {}).get("reason") in BLOCKING or result["special_population"]["under_13"]:
        return {"level": "blocked", **out}
    if core_resolved and len(sup_known) >= r["ready_supporting_ratio"] * len(spec["supporting"]):
        level = "ready"
    elif len(core_known) >= r["preliminary_core_ratio"] * len(spec["core"]):
        level = "preliminary"
    else:
        level = "not_ready"
    return {"level": level, **out}


# ---------- plan ----------


def plan_skeleton(goal: str, facts: dict[str, Fact], skipped: set[str], now: datetime) -> dict:
    """What the plan may be built from: engine findings (never its services), the goal's metrics and what's missing.
    The LLM drafts the plan from this; validate_plan() checks the draft."""
    result = eng.evaluate(engine_answers(goal, facts))
    ready = readiness(goal, facts, skipped, now, result)
    slots = rules()["slots"]
    return {
        "goal": goal,
        "goal_label": goal_spec(goal)["label"],
        "readiness": ready["level"],
        "confidence": "standard" if ready["level"] == "ready" else "preliminary",
        "findings": result["findings"],
        "energy_need_kcal": result["energy_need_kcal"],
        "weight_loss_target_kcal": result["weight_loss_target_kcal"],
        # Clinically indicated checks are something to discuss with a clinician, not a referral
        "discuss_with_clinician": [t["label"] for t in result["tests"] if t["grade"] == "A"],
        "cautions": result["notes"],
        "handoff": result["handoff"],
        "limitations": [f"Not known yet: {slots[s]['label']}" for s in ready["missing"]]
        + [f"Skipped: {slots[s]['label']}" for s in ready["skipped"]],
        "metric_options": goal_spec(goal)["metrics"],
        "rules_version": f"{rules()['version']}+{result['rules_version']}",
    }


PLAN_LISTS = ("priorities", "daily_habits", "weekly_habits", "metrics")


def validate_plan(plan: dict, banned_terms: list[str]) -> list[str]:
    """Problems with a drafted plan. banned_terms: provider and product names – a plan never sells anything."""
    p = rules()["plan"]
    plan = plan if isinstance(plan, dict) else {}
    problems: list[str] = []
    if not isinstance(plan.get("situation_summary"), str) or not plan["situation_summary"].strip():
        problems.append("situation_summary is required")
    for key in PLAN_LISTS:
        items = plan.get(key)
        if not isinstance(items, list) or not all(isinstance(i, str) and i.strip() for i in items):
            problems.append(f"{key} must be a list of short texts")
    if isinstance(plan.get("priorities"), list) and not 1 <= len(plan["priorities"]) <= p["max_priorities"]:
        problems.append(f"priorities: 1 to {p['max_priorities']} items")
    for key in ("daily_habits", "metrics"):
        if isinstance(plan.get(key), list) and not plan[key]:
            problems.append(f"{key}: at least one item")
    lo, hi = p["review_days"]
    days = plan.get("review_in_days")
    if isinstance(days, bool) or not isinstance(days, int) or not lo <= days <= hi:
        problems.append(f"review_in_days must be a whole number from {lo} to {hi}")
    text = " ".join(str(v) for v in plan.values()).lower()
    hits = [t for t in banned_terms if t and t.lower() in text]
    if hits:
        problems.append(f"a plan must not name providers or products: {hits}")
    if "$" in text:
        problems.append("a plan must not mention prices")
    return problems
