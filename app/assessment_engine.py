"""Weight, nutrition and gut assessment rules – pure functions over app/assessment_rules/weight_gut.yaml.

No DB and no LLM: the agent turns the user's words into fields, this module decides what they mean. Any field may be
missing (None / "unknown" / "skipped"); missing data is reported as unknown, never guessed.
See docs/WEIGHT_GUT_ASSESSMENT.md for the evidence behind each table.
"""

import re
from functools import lru_cache
from pathlib import Path

import yaml

RULES_FILE = Path(__file__).parent / "assessment_rules" / "weight_gut.yaml"
BRANDS_DIR = Path(__file__).parent / "brands"
MAX_RECOMMENDATIONS = 2

UNDERWEIGHT = {"bmi_severe_under", "bmi_under"}
OVERWEIGHT = {"bmi_over_low", "bmi_over_high", "bmi_ob1", "bmi_ob2", "bmi_ob3"}
IMPROVE_FLAGS = {"sugary_drinks", "large_portions", "low_plants", "low_protein", "limited_variety",
                 "frequent_alcohol", "late_night"}
CONCERN_FLAGS = {"restrictive", "meal_skipping_fatigue"}
# Not offered for restrictive or disordered eating, minors or pregnancy
CAUTION_BLOCKED_SERVICES = {"s_medical_weight", "s_glp1_program", "s_medspa", "s_fitness", "s_dna_test"}
GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "X": 3}


@lru_cache
def rules() -> dict:
    return yaml.safe_load(RULES_FILE.read_text(encoding="utf-8"))


@lru_cache
def catalog(brand: str) -> dict:
    return yaml.safe_load((BRANDS_DIR / f"{brand}_catalog.yaml").read_text(encoding="utf-8"))


def known(value) -> bool:
    if value is None or value == [] or value == {}:
        return False
    return not (isinstance(value, str) and value.strip().lower() in ("", "unknown", "skipped"))


def _codes(value) -> list[str]:
    return [v for v in value if isinstance(v, str) and known(v)] if isinstance(value, list) else []


def _num(value, lo: float, hi: float) -> float | None:
    """A number within [lo, hi], or None. Numeric strings ("70") are accepted; booleans, text and lists are not."""
    if isinstance(value, bool) or not known(value):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if lo <= x <= hi else None


def _yes(value) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() in ("yes", "true", "y"))


def _band(rows: list[dict], value: float) -> dict:
    return next(r for r in rows if "below" not in r or value < r["below"])


def _round10(x: float) -> int:
    return int(round(x / 10.0) * 10)


# ---------- units ----------


def lb_to_kg(lb: float) -> float:
    return round(lb * 0.45359237, 1)


def ft_in_to_cm(feet: float, inches: float = 0) -> float:
    return round((feet * 12 + inches) * 2.54, 1)


def age_years(age=None, age_band=None) -> float | None:
    """Exact age, or the midpoint of a band like "30-39" ("65+" → 65)."""
    if _num(age, 1, 120) is not None:
        return _num(age, 1, 120)
    if known(age_band):
        nums = [int(n) for n in re.findall(r"\d+", str(age_band))]
        if len(nums) >= 2:
            return _num((nums[0] + nums[1]) / 2, 1, 120)
        if nums:
            return _num(nums[0], 1, 120)
    return None


# ---------- body ----------


def bmi(weight_kg, height_cm) -> tuple[float, str] | None:
    weight_kg, height_cm = _num(weight_kg, 25, 350), _num(height_cm, 100, 250)
    if weight_kg is None or height_cm is None:
        return None  # missing or implausible – ask again rather than compute
    value = round(weight_kg / (height_cm / 100) ** 2, 1)
    return value, _band(rules()["bmi"], value)["code"]


def resting_energy(weight_kg: float, height_cm: float, age: float, sex: str) -> float:
    """Mifflin-St Jeor (1990)."""
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return base + 5 if sex == "male" else base - 161


def energy_need(weight_kg=None, height_cm=None, age=None, sex=None, activity_level=None) -> tuple[int, int] | None:
    """Daily need as a (low, high) range. Unknown sex or activity widens the range instead of guessing."""
    e = rules()["energy"]
    weight_kg, height_cm, age = _num(weight_kg, 25, 350), _num(height_cm, 100, 250), _num(age, 1, 120)
    sexes = [sex] if sex in ("female", "male") else ["female", "male"]
    levels = [activity_level] if activity_level in e["activity_factors"] else ["inactive", "meets_guideline"]
    if all(x is not None for x in (weight_kg, height_cm, age)):
        values = [resting_energy(weight_kg, height_cm, age, s) * e["activity_factors"][lv] for s in sexes for lv in levels]
    elif age is not None or weight_kg is not None:
        rows = e["dga_fallback"] if age is None else [[r for r in e["dga_fallback"] if r["min_age"] <= age][-1]]
        values = [v for r in rows for s in sexes for lv in levels
                  for v in r[s]["sedentary" if lv in ("inactive", "low") else "active"]]
    else:
        return None
    return _round10(min(values)), _round10(max(values))


def weight_loss_target(need: tuple[int, int], sex=None) -> tuple[int, int]:
    e = rules()["energy"]
    floor = e["min_kcal"]["female"] if sex == "female" else e["min_kcal"]["male"]  # unknown sex: the higher floor
    small, large = e["deficit_kcal"]
    return max(need[0] - large, floor), max(need[1] - small, floor)


# ---------- exercise ----------


def _category(cats: set[str]) -> str:
    if "E" in cats or {"C", "D"} <= cats:
        return "E"
    return next((c for c in ("C", "D", "F", "B") if c in cats), "A")


def exercise_profile(activities, weight_kg=None) -> dict | None:
    """activities: [{code, sessions_per_week, minutes, intensity?}]; [] means "no exercise"; None = not answered."""
    if not isinstance(activities, list):
        return None
    ex = rules()["exercise"]
    weight_kg = _num(weight_kg, 25, 350)
    # A bare code ("run_6") is an activity with unknown frequency; anything else unreadable is dropped
    activities = [{"code": a} if isinstance(a, str) else a for a in activities if isinstance(a, (str, dict))]
    cats: set[str] = set()
    minutes = {"light": 0.0, "moderate": 0.0, "vigorous": 0.0}
    kcal, volume_known = 0.0, not activities
    for a in activities:
        code, intensity = a.get("code"), a.get("intensity")
        spec = ex["activities"].get(code if isinstance(code, str) else "other", ex["activities"]["other"])
        cats.add(spec["category"])
        met = spec["met"] or ex["other_intensity_met"].get(intensity if isinstance(intensity, str) else "moderate", 4.5)
        sessions, mins = _num(a.get("sessions_per_week"), 0, 21), _num(a.get("minutes"), 0, 600)
        if sessions is None or mins is None:
            continue
        volume_known = True
        weekly = sessions * mins
        key = "vigorous" if met >= ex["vigorous_met"] else "moderate" if met >= ex["moderate_met"] else "light"
        minutes[key] += weekly
        if weight_kg is not None:
            kcal += met * weight_kg * weekly / 60
    eq = minutes["moderate"] + 2 * minutes["vigorous"]
    category = _category(cats)
    return {
        "category": category,
        "category_label": ex["categories"][category]["label"],
        "next_step": ex["categories"][category]["next_step"],
        "weekly_moderate_eq_min": round(eq) if volume_known else None,
        "light_min": round(minutes["light"]) if volume_known else None,
        "activity_level": _band(ex["levels"], eq)["code"] if volume_known else None,
        "kcal_week": round(kcal) if volume_known and weight_kg is not None else None,
        "has_strength": bool(cats & {"D", "E"}),
        "has_cardio": bool(cats & {"C", "E"}),
    }


# ---------- diet ----------


def intake_band(total_kcal, need: tuple[int, int] | None, flags: list[str]) -> str:
    d = rules()["diet"]["intake"]
    high_energy = len(set(flags) & set(d["high_energy_flags"]))
    if high_energy >= 2:
        return "intake_likely_above"
    if known(total_kcal) and need:
        if total_kcal > need[1] * d["above_ratio"]:
            return "intake_likely_above"
        if total_kcal < need[0] * d["below_ratio"]:
            return "intake_likely_below"
    if "meal_skipping_fatigue" in flags:
        return "intake_likely_below"
    if (known(total_kcal) and need) or ("balanced" in flags and not high_energy):
        return "intake_about_right"
    return "intake_unclear"


def diet_class(flags: list[str], intake_code: str | None, bmi_code: str | None) -> str | None:
    f = set(flags)
    n = len(f & IMPROVE_FLAGS)
    if f & CONCERN_FLAGS or n >= 4 or (intake_code == "intake_likely_below" and bmi_code in UNDERWEIGHT):
        return "potentially_unhealthy"
    if n or intake_code == "intake_likely_above":
        return "needs_improvement"
    if "balanced" in f:
        return "healthy_balanced"
    return None  # not enough detail


# ---------- gut and tests ----------


def gut_route(gi_codes: list[str], family_codes: list[str]) -> dict:
    g = rules()["gut"]
    symptoms = [c for c in gi_codes if c in g["symptoms"]]
    return {
        "red_flags": [c for c in gi_codes if c in g["red_flags"]],
        "symptoms": symptoms,
        "family_history": [c for c in family_codes if c in g["family_history"]],
        "persistent": any(g["symptoms"][c]["persistent"] for c in symptoms),
    }


def pick_tests(testing_goal, gut: dict, *, bmi_code=None, age=None, activity_level=None, diet_plans=(),
               diet_flags=(), glp1=None, max_n: int = MAX_RECOMMENDATIONS) -> list[str]:
    """Clinically indicated tests (symptoms, family history, risk) plus optional tests matching the user's goal.
    goal "none" → no tests. Grade A first."""
    if testing_goal == "none" or gut["red_flags"]:
        return []
    g, t = rules()["gut"], rules()["tests"]
    indicated: list[str] = []
    for c in gut["symptoms"]:
        if g["symptoms"][c]["persistent"]:
            indicated += g["symptoms"][c]["tests"]
    for c in gut["family_history"]:
        indicated += g["family_history"][c]["tests"]
    risk_factor = activity_level in ("inactive", "low") or "fh_diabetes" in gut["family_history"]
    if bmi_code in OVERWEIGHT and (risk_factor or (age or 0) >= 35):
        indicated.append("t_hba1c")
    if {"vegan", "carnivore"} & set(diet_plans) or "restrictive" in diet_flags or glp1 in ("effective", "side_effects"):
        indicated.append("t_nutrients")
    if testing_goal == "inherited_risk" and bmi_code in OVERWEIGHT:
        indicated.append("t_lipids")

    # Limited-evidence tests only when nothing clinically indicated is on the table
    optional = [] if indicated else [
        code for code, spec in t.items()
        if known(testing_goal) and testing_goal in spec["goals"] and not spec.get("needs_history") and spec["grade"] in ("C", "X")
    ]
    picked: list[str] = []
    for code in sorted(dict.fromkeys(indicated), key=lambda c: GRADE_ORDER[t[c]["grade"]]) + optional:
        if code not in picked:
            picked.append(code)
    return picked[:max_n]


# ---------- recommendations ----------


def _product(service: str, brand: str | None) -> dict | None:
    if not brand:
        return None
    cat = catalog(brand)
    if service in cat.get("referrals", {}):
        return {"referral": cat["referrals"][service]["text"]}
    for p in cat["products"]:
        if p["recommend"] == "assess" and service in p["serves"]:
            return {"code": p["code"], "name": p["name"], "price": p["price"], "kind": p["kind"]}
    return None


def evaluate(answers: dict, brand: str | None = None) -> dict:
    """answers: {step: {field: value}} for steps q0, a1_q1, a1_q2, a1_q3, a2_q1, a2_q2, a2_q3."""
    r = rules()
    answers = answers if isinstance(answers, dict) else {}
    q0, body, move, eat, plan, gi, test_q = (
        answers[k] if isinstance(answers.get(k), dict) else {}
        for k in ("q0", "a1_q1", "a1_q2", "a1_q3", "a2_q1", "a2_q2", "a2_q3")
    )
    goal = q0.get("goal") if isinstance(q0.get("goal"), str) and known(q0.get("goal")) else None
    age = age_years(body.get("age"), body.get("age_band"))
    sex = body.get("sex") if body.get("sex") in ("female", "male") else None
    kg, cm = _num(body.get("weight_kg"), 25, 350), _num(body.get("height_cm"), 100, 250)
    diet_flags, patterns = _codes(eat.get("diet_flags")), _codes(plan.get("eating_patterns"))
    diet_plans, glp1 = _codes(plan.get("diet_plans")), plan.get("glp1")
    gut = gut_route(_codes(gi.get("gi_symptoms")), _codes(gi.get("family_history")))

    minor = age is not None and age < 18
    pregnancy = _yes(body.get("pregnant")) or _yes(body.get("breastfeeding"))
    adult_rules = not (minor or pregnancy)
    b = bmi(kg, cm) if adult_rules else None
    bmi_code = b[1] if b else None
    ex = exercise_profile(move.get("activities"), kg)
    activity = ex["activity_level"] if ex else None
    need = energy_need(kg, cm, age, sex, activity) if adult_rules else None
    intake = None
    foods = [f for f in eat.get("foods") or [] if isinstance(f, dict)] if isinstance(eat.get("foods"), list) else []
    if _num(eat.get("meals_per_day"), 0, 12) is not None or diet_flags or foods:
        total = sum(_num(f.get("kcal_est"), 0, 5000) or 0 for f in foods) or None
        intake = intake_band(total, need, diet_flags)
    dclass = diet_class(diet_flags, intake, bmi_code) if intake or diet_flags else None
    nutrition_concern = bool(
        "restrictive" in diet_flags or {"binge_like", "very_low"} & set(patterns) or bmi_code == "bmi_severe_under"
        or (bmi_code in UNDERWEIGHT and goal == "lose_weight")
    )

    sections = {
        "goal": goal is not None,
        "body": any(known(body.get(k)) for k in ("weight_kg", "height_cm", "age", "age_band")),
        "exercise": ex is not None,
        "nutrition": intake is not None,
        "diet_plan": bool(diet_plans or patterns or known(glp1)),
        "gut": "gi_symptoms" in gi and known(gi.get("gi_symptoms")) or bool(gut["family_history"]),
        "testing_goal": known(test_q.get("testing_goal")),
    }

    # Findings the reply can explain (texts come from the YAML)
    findings: list[dict] = []
    if b:
        row = next(x for x in r["bmi"] if x["code"] == bmi_code)
        findings.append({"area": "bmi", "code": bmi_code, "value": b[0], "text": f"BMI {b[0]} – {row['interpretation']}"})
    if ex:
        findings.append({"area": "exercise", "code": ex["category"], "text": ex["category_label"],
                         "level": activity, "next_step": ex["next_step"]})
    if intake and not (intake == "intake_unclear" and diet_flags):  # the flags already say what we know
        findings.append({"area": "intake", "code": intake, "text": r["diet"]["intake"]["bands"][intake]})
    for f in diet_flags:
        spec = r["diet"]["flags"].get(f)
        if spec:
            findings.append({"area": "diet", "code": f, "text": spec["issue"], "next_step": spec["recommendation"]})
    for p in diet_plans:
        spec = r["diet_plans"].get(p)
        if spec and p != "none":
            findings.append({"area": "diet_plan", "code": p, "text": spec["label"], "gaps": spec["gaps"], "next_step": spec["tip"]})
    for p in patterns:
        spec = r["eating_patterns"].get(p)
        if spec:
            findings.append({"area": "eating_pattern", "code": p, "text": spec["label"], "next_step": spec["suggestion"]})
    for c in gut["symptoms"]:
        spec = r["gut"]["symptoms"][c]
        findings.append({"area": "gut", "code": c, "text": spec["label"], "next_step": spec["action"]})

    # Notes and handoff
    notes: list[str] = []
    handoff = None
    if gut["red_flags"]:
        handoff = {"reason": "gi_red_flag", "urgency": "urgent"}
        notes.append(r["notes"]["red_flag"])
    elif minor or pregnancy:
        handoff = {"reason": "special_population", "urgency": "routine"}
        notes.append(r["notes"]["special_population"])
    elif nutrition_concern:
        handoff = {"reason": "nutrition_concern", "urgency": "routine"}
        notes.append(r["notes"]["nutrition_concern"])
    elif glp1 == "side_effects":
        handoff = {"reason": "glp1_side_effects", "urgency": "routine"}
    if bmi_code in ("bmi_over_low", "bmi_over_high") and ex and ex["has_strength"] and activity in ("meets_guideline", "high"):
        notes.append(r["notes"]["bmi_muscle"])
    if bmi_code in ("bmi_over_low", "bmi_over_high") and (age or 0) >= 65:
        notes.append(r["notes"]["bmi_older"])
    if glp1 == "side_effects":
        notes.append(r["notes"]["glp1_side_effects"])
    if not any(sections.values()):
        notes.append(r["notes"]["no_info"])

    # Candidate services in priority order: safety → nutrition → exercise → testing / referral
    candidates: list[tuple[str, str]] = []  # (rule, service)
    if nutrition_concern or dclass == "potentially_unhealthy":
        candidates.append(("r_restrictive", "s_dietitian"))
    if glp1 == "side_effects":
        candidates.append(("r_glp1_side", "s_dietitian"))
    if gut["persistent"]:
        candidates.append(("r_bloating", "s_gut_eval"))
    if goal == "body_shape" and bmi_code in ("bmi_healthy",) and not nutrition_concern:
        candidates.append(("r_med_spa", "s_medspa"))
    if glp1 == "effective":
        candidates.append(("r_glp1_ok", "s_glp1_program"))
    if len(set(diet_flags) & IMPROVE_FLAGS) >= 2 and activity in ("inactive", "low"):
        candidates.append(("r_multi", "s_dna_coaching"))
    if intake == "intake_likely_above":
        candidates.append(("r_excess", "s_dna_coaching"))
    if "low_protein" in diet_flags and ex and ex["has_strength"]:
        candidates.append(("r_low_protein_strength", "s_sports_nutrition"))
    elif ex and ex["has_strength"] and not sections["nutrition"]:
        candidates.append(("r_strength_recovery", "s_sports_nutrition"))  # brief §A: support recovery nutrition
    on_glp1 = glp1 in ("effective", "side_effects") or "glp1" in diet_plans
    if goal == "lose_weight" and bmi_code in ("bmi_ob1", "bmi_ob2", "bmi_ob3") and not on_glp1:
        candidates.append(("r_medical_weight", "s_medical_weight"))
    if activity in ("inactive", "low"):
        candidates.append(("r_inactive", "s_fitness"))
    elif ex and ex["category"] == "C":
        candidates.append(("r_cardio_only", "s_fitness"))
    if dclass == "needs_improvement":
        candidates.append(("r_diet_improve", "s_dna_coaching"))

    tests = pick_tests(test_q.get("testing_goal"), gut, bmi_code=bmi_code, age=age, activity_level=activity,
                       diet_plans=diet_plans, diet_flags=diet_flags, glp1=glp1) if adult_rules or gut["persistent"] else []
    # Tests for ongoing symptoms are arranged by the clinician doing the gut evaluation
    symptom_tests = {t for c in gut["symptoms"] if r["gut"]["symptoms"][c]["persistent"] for t in r["gut"]["symptoms"][c]["tests"]}
    test_services: dict[str, list[str]] = {}
    for code in tests:
        service = "s_gut_eval" if code in symptom_tests else r["tests"][code]["service"]
        if service:
            test_services.setdefault(service, []).append(code)
            candidates.append(("r_test", service))

    if not candidates and any(sections.values()) and handoff is None:
        candidates.append(("r_optional", "s_dna_coaching"))

    # Hard limits: red flag → nothing; special population / nutrition concern → no weight-loss services; ≤ 2 items
    if gut["red_flags"] or (age is not None and age < 13):
        candidates = []
    elif minor or pregnancy or nutrition_concern:
        candidates = [c for c in candidates if c[1] not in CAUTION_BLOCKED_SERVICES]
    services: list[dict] = []
    for rule, service in candidates:
        if service in (s["service"] for s in services):
            continue
        if len(services) == MAX_RECOMMENDATIONS:
            break
        item = {"service": service, "rule": rule, "label": r["services"][service]["label"],
                "kind": r["services"][service]["kind"], "tests": test_services.get(service, [])}
        item["product"] = _product(service, brand)
        services.append(item)

    offered_tests = {t for s in services for t in s["tests"]}
    # Optional tests with no bookable service (e.g. microbiome) can still be explained, within the cap
    extra = [c for c in tests if c not in offered_tests and not r["tests"][c]["service"]]
    test_items = [
        {"code": c, "label": r["tests"][c]["label"], "grade": r["tests"][c]["grade"], "limitation": r["tests"][c]["limitation"]}
        for c in tests if c in offered_tests or c in extra[: max(0, MAX_RECOMMENDATIONS - len(services))]
    ]

    result = {
        "rules_version": r["version"],
        "case": _case(sections, gut, dclass, intake),
        "sections": sections,
        "goal": goal,
        "special_population": {"minor": minor, "pregnancy": pregnancy, "under_13": age is not None and age < 13},
        "nutrition_concern": nutrition_concern,
        "bmi": {"value": b[0], "code": bmi_code} if b else None,
        "energy_need_kcal": list(need) if need else None,
        "weight_loss_target_kcal": None,
        "exercise": ex,
        "intake": intake,
        "diet_class": dclass,
        "gut": gut,
        "findings": findings,
        "tests": test_items,
        "services": services,
        "handoff": handoff,
        "notes": notes,
    }
    if need and goal == "lose_weight" and bmi_code in OVERWEIGHT and not nutrition_concern:
        result["weight_loss_target_kcal"] = list(weight_loss_target(need, sex))
    return result


def _case(sections: dict, gut: dict, dclass: str | None, intake: str | None) -> str:
    """The brief's A–G partial-information cases."""
    areas = {k for k in ("exercise", "nutrition", "gut", "testing_goal") if sections[k]}
    if not any(sections.values()):
        return "G"
    if areas == {"exercise", "nutrition", "gut", "testing_goal"}:
        return "E"
    if gut["symptoms"] and set(gut["symptoms"]) != {"gi_none"} or gut["red_flags"]:
        return "D"
    if dclass == "potentially_unhealthy" or (intake == "intake_unclear" and dclass is None):
        return "C"
    if areas == {"exercise"}:
        return "A"
    if areas == {"nutrition"}:
        return "B"
    return "F"
