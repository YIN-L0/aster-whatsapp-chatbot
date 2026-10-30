import re
from pathlib import Path

import pytest

from app import assessment_engine as eng

BRAND = "helix_harvest"
DOC = Path(__file__).parent.parent / "docs" / "WEIGHT_GUT_ASSESSMENT.md"


def _services(result: dict) -> list[str]:
    return [s["service"] for s in result["services"]]


# ---------- body ----------


@pytest.mark.parametrize(
    "value, code",
    [
        (15.9, "bmi_severe_under"), (16.0, "bmi_under"), (18.4, "bmi_under"), (18.5, "bmi_healthy"),
        (24.9, "bmi_healthy"), (25.0, "bmi_over_low"), (27.4, "bmi_over_low"), (27.5, "bmi_over_high"),
        (29.9, "bmi_over_high"), (30.0, "bmi_ob1"), (34.9, "bmi_ob1"), (35.0, "bmi_ob2"), (39.9, "bmi_ob2"),
        (40.0, "bmi_ob3"),
    ],
)
def test_bmi_band_edges(value, code):
    # At 200 cm, BMI = weight / 4
    assert eng.bmi(value * 4, 200) == (value, code)


def test_bmi_needs_both_values_and_plausible_numbers():
    assert eng.bmi(None, 170) is None
    assert eng.bmi(70, "skipped") is None
    assert eng.bmi(70, 17) is None  # height given in metres by mistake – ask again


def test_unit_conversions_and_age_bands():
    assert eng.lb_to_kg(180) == 81.6
    assert eng.ft_in_to_cm(5, 6) == 167.6
    assert eng.age_years(age_band="30-39") == 34.5
    assert eng.age_years(age_band="65+") == 65
    assert eng.age_years(age=41, age_band="30-39") == 41
    assert eng.age_years() is None


def test_energy_need_mifflin_by_sex_and_unknown_sex_gives_a_range():
    # Mifflin resting: female 70 kg, 165 cm, 30 y = 1420.25; male = 1586.25; × 1.2 inactive
    assert eng.energy_need(70, 165, 30, "female", "inactive") == (1700, 1700)
    assert eng.energy_need(70, 165, 30, "male", "inactive") == (1900, 1900)
    assert eng.energy_need(70, 165, 30, None, "inactive") == (1700, 1900)


def test_energy_need_unknown_activity_widens_and_fallback_uses_dga():
    low, high = eng.energy_need(70, 165, 30, "female", None)
    assert (low, high) == (1700, 2200)  # inactive 1.2 … meets_guideline 1.55
    assert eng.energy_need(age=30, sex="female", activity_level="inactive") == (1600, 2000)
    assert eng.energy_need(age=70, sex="male", activity_level="high") == (2400, 2600)
    assert eng.energy_need() is None


def test_weight_loss_target_never_below_the_floor():
    assert eng.weight_loss_target((1500, 1500), "female") == (1200, 1200)
    assert eng.weight_loss_target((2400, 2400), "female") == (1900, 2100)
    assert eng.weight_loss_target((1700, 1700), None) == (1500, 1500)  # unknown sex: the higher floor


# ---------- exercise ----------


def test_exercise_vigorous_minutes_count_double():
    p = eng.exercise_profile([{"code": "run_6", "sessions_per_week": 3, "minutes": 30}], weight_kg=70)
    assert p["category"] == "C"
    assert p["weekly_moderate_eq_min"] == 180
    assert p["activity_level"] == "meets_guideline"


def test_exercise_levels_and_categories():
    assert eng.exercise_profile([{"code": "walk_brisk", "sessions_per_week": 2, "minutes": 20}])["activity_level"] == "low"
    yoga = eng.exercise_profile([{"code": "yoga_hatha", "sessions_per_week": 5, "minutes": 60}])
    assert (yoga["category"], yoga["activity_level"], yoga["light_min"]) == ("B", "inactive", 300)
    both = eng.exercise_profile([{"code": "weights", "sessions_per_week": 2, "minutes": 45},
                                 {"code": "jog", "sessions_per_week": 2, "minutes": 30}])
    assert both["category"] == "E" and both["has_strength"] and both["has_cardio"]
    none = eng.exercise_profile([])
    assert (none["category"], none["activity_level"]) == ("A", "inactive")
    assert eng.exercise_profile(None) is None


def test_exercise_without_frequency_keeps_category_but_not_level():
    p = eng.exercise_profile([{"code": "tennis_singles"}])
    assert p["category"] == "F"
    assert p["activity_level"] is None and p["weekly_moderate_eq_min"] is None


def test_exercise_kcal_uses_met_weight_and_hours():
    p = eng.exercise_profile([{"code": "run_6", "sessions_per_week": 2, "minutes": 30}], weight_kg=70)
    assert p["kcal_week"] == 651  # 9.3 MET × 70 kg × 1 h
    assert eng.exercise_profile([{"code": "other", "intensity": "vigorous", "sessions_per_week": 1, "minutes": 60}])[
        "weekly_moderate_eq_min"] == 120


# ---------- diet ----------


def test_intake_band():
    need = (1800, 2000)
    assert eng.intake_band(None, need, ["sugary_drinks", "late_night"]) == "intake_likely_above"
    assert eng.intake_band(2400, need, []) == "intake_likely_above"
    assert eng.intake_band(1100, need, []) == "intake_likely_below"
    assert eng.intake_band(None, need, ["meal_skipping_fatigue"]) == "intake_likely_below"
    assert eng.intake_band(1900, need, []) == "intake_about_right"
    assert eng.intake_band(None, None, ["insufficient_detail"]) == "intake_unclear"


def test_diet_class():
    assert eng.diet_class(["balanced"], "intake_about_right", "bmi_healthy") == "healthy_balanced"
    assert eng.diet_class(["sugary_drinks"], "intake_about_right", None) == "needs_improvement"
    assert eng.diet_class([], "intake_likely_above", None) == "needs_improvement"
    assert eng.diet_class(["restrictive"], None, None) == "potentially_unhealthy"
    assert eng.diet_class(["sugary_drinks", "low_plants", "low_protein", "late_night"], None, None) == "potentially_unhealthy"
    assert eng.diet_class([], "intake_likely_below", "bmi_under") == "potentially_unhealthy"
    assert eng.diet_class([], "intake_unclear", None) is None


# ---------- gut and tests ----------


def test_pick_tests_goal_none_and_red_flag_give_nothing():
    gut = eng.gut_route(["bloat_frequent"], ["fh_celiac"])
    assert eng.pick_tests("none", gut) == []
    assert eng.pick_tests("understand_symptoms", eng.gut_route(["blood_in_stool", "dairy"], [])) == []


def test_pick_tests_indicated_grade_a_first_and_capped():
    gut = eng.gut_route(["diarrhea_persistent", "dairy"], ["fh_ibd"])
    picked = eng.pick_tests("understand_symptoms", gut)
    assert len(picked) == 2
    assert all(eng.rules()["tests"][c]["grade"] == "A" for c in picked)


def test_pick_tests_optional_tests_only_when_the_goal_matches():
    no_history = eng.gut_route([], [])
    assert eng.pick_tests("genetics_nutrition", no_history) == ["t_nutrigenetic"]
    assert eng.pick_tests("inherited_risk", no_history) == []  # FH / metabolic panels need a family history
    assert eng.pick_tests("inherited_risk", eng.gut_route([], ["fh_high_cholesterol"])) == ["t_fh"]


def test_pick_tests_metabolic_screening_follows_ada_risk():
    gut = eng.gut_route([], [])
    assert "t_hba1c" in eng.pick_tests(None, gut, bmi_code="bmi_ob1", age=40)
    assert "t_hba1c" in eng.pick_tests(None, gut, bmi_code="bmi_over_low", age=28, activity_level="inactive")
    assert eng.pick_tests(None, gut, bmi_code="bmi_healthy", age=40) == []


# ---------- evaluate: the brief's cases and hard rules ----------


FULL = {
    "q0": {"goal": "lose_weight"},
    "a1_q1": {"weight_kg": 92, "height_cm": 170, "age": 42, "sex": "female"},
    "a1_q2": {"activities": [{"code": "walk_moderate", "sessions_per_week": 2, "minutes": 30}]},
    "a1_q3": {"meals_per_day": 3, "foods": [{"item": "latte", "kcal_est": 300}, {"item": "burger meal", "kcal_est": 1200},
                                            {"item": "pasta", "kcal_est": 1000}],
              "diet_flags": ["sugary_drinks", "large_portions", "low_plants"]},
    "a2_q1": {"diet_plans": ["none"], "glp1": "none"},
    "a2_q2": {"gi_symptoms": ["bloat_frequent"], "family_history": ["fh_diabetes"]},
    "a2_q3": {"testing_goal": "understand_symptoms"},
}


def test_full_assessment_case_e():
    r = eng.evaluate(FULL, BRAND)
    assert r["case"] == "E"
    assert r["bmi"] == {"value": 31.8, "code": "bmi_ob1"}
    assert r["intake"] == "intake_likely_above" and r["diet_class"] == "needs_improvement"
    assert _services(r) == ["s_gut_eval", "s_dna_coaching"]
    # Symptom tests ride on the gut evaluation instead of taking a third slot
    assert set(r["services"][0]["tests"]) == {"t_celiac", "t_lactose"}
    assert r["services"][1]["product"]["code"] == "hh_coach_membership"
    assert r["weight_loss_target_kcal"] is not None
    assert r["handoff"] is None


@pytest.mark.parametrize(
    "answers",
    [
        FULL,
        {**FULL, "a2_q1": {"diet_plans": ["vegan"], "glp1": "effective"}, "a2_q2": {"gi_symptoms": ["dairy", "gluten"],
                                                                                      "family_history": ["fh_high_cholesterol"]}},
        {**FULL, "q0": {"goal": "body_shape"}, "a1_q1": {"weight_kg": 60, "height_cm": 170, "age": 30}},
    ],
)
def test_never_more_than_two_recommendations(answers):
    r = eng.evaluate(answers, BRAND)
    assert len(r["services"]) + len([t for t in r["tests"] if not any(t["code"] in s["tests"] for s in r["services"])]) <= 2


def test_ask_only_products_are_never_recommended():
    ask = {p["code"] for p in eng.catalog(BRAND)["products"] if p["recommend"] == "ask"}
    for answers in (FULL, {"a1_q2": {"activities": []}}, {"a2_q1": {"glp1": "effective", "diet_plans": ["glp1"]}}):
        r = eng.evaluate(answers, BRAND)
        assert not {(s["product"] or {}).get("code") for s in r["services"]} & ask


def test_case_a_exercise_only_strength_gets_recovery_nutrition():
    r = eng.evaluate({"a1_q2": {"activities": [{"code": "weights", "sessions_per_week": 3, "minutes": 60}]}}, BRAND)
    assert r["case"] == "A"
    assert _services(r)[0] == "s_sports_nutrition"
    assert r["intake"] is None  # nutrition stays unknown


def test_case_b_nutrition_only():
    r = eng.evaluate({"a1_q3": {"meals_per_day": 3, "diet_flags": ["sugary_drinks", "large_portions"]}}, BRAND)
    assert r["case"] == "B"
    assert "s_dna_coaching" in _services(r)
    assert r["exercise"] is None


def test_case_d_gut_red_flag_escalates_and_offers_nothing():
    r = eng.evaluate({**FULL, "a2_q2": {"gi_symptoms": ["blood_in_stool", "bloat_frequent"]}}, BRAND)
    assert r["handoff"] == {"reason": "gi_red_flag", "urgency": "urgent"}
    assert r["services"] == [] and r["tests"] == []


def test_case_g_no_information():
    r = eng.evaluate({}, BRAND)
    assert r["case"] == "G"
    assert r["services"] == [] and r["handoff"] is None
    assert r["notes"]


def test_med_spa_only_for_body_shape_with_healthy_bmi():
    healthy = eng.evaluate({"q0": {"goal": "body_shape"}, "a1_q1": {"weight_kg": 60, "height_cm": 168}}, BRAND)
    assert _services(healthy)[0] == "s_medspa"
    assert "Essence" in healthy["services"][0]["product"]["referral"]
    heavier = eng.evaluate({"q0": {"goal": "body_shape"}, "a1_q1": {"weight_kg": 80, "height_cm": 168}}, BRAND)
    assert "s_medspa" not in _services(heavier)


def test_restrictive_eating_hands_off_and_blocks_weight_loss_services():
    r = eng.evaluate({"q0": {"goal": "lose_weight"}, "a1_q1": {"weight_kg": 50, "height_cm": 170, "age": 24},
                      "a1_q2": {"activities": []},
                      "a1_q3": {"meals_per_day": 1, "diet_flags": ["restrictive"]}}, BRAND)
    assert r["nutrition_concern"]
    assert r["handoff"] == {"reason": "nutrition_concern", "urgency": "routine"}
    assert _services(r)[0] == "s_dietitian"
    assert not set(_services(r)) & eng.CAUTION_BLOCKED_SERVICES  # a nutrient blood test may follow
    assert r["weight_loss_target_kcal"] is None


@pytest.mark.parametrize("body", [{"age": 16, "weight_kg": 70, "height_cm": 165},
                                  {"age": 31, "weight_kg": 70, "height_cm": 165, "pregnant": True}])
def test_minors_and_pregnancy_skip_bmi_and_calories_and_hand_off(body):
    r = eng.evaluate({"q0": {"goal": "lose_weight"}, "a1_q1": body, "a1_q2": {"activities": []}}, BRAND)
    assert r["bmi"] is None and r["energy_need_kcal"] is None
    assert r["handoff"] == {"reason": "special_population", "urgency": "routine"}
    assert not set(_services(r)) & eng.CAUTION_BLOCKED_SERVICES


def test_under_13_gets_no_services():
    r = eng.evaluate({"a1_q1": {"age": 11}, "a1_q3": {"meals_per_day": 3, "diet_flags": ["sugary_drinks"]}}, BRAND)
    assert r["special_population"]["under_13"]
    assert r["services"] == []


def test_muscle_note_for_strength_trained_overweight_bmi():
    r = eng.evaluate({"a1_q1": {"weight_kg": 88, "height_cm": 180, "age": 30, "sex": "male"},
                      "a1_q2": {"activities": [{"code": "weights", "sessions_per_week": 4, "minutes": 60}]}}, BRAND)
    assert r["bmi"]["code"] == "bmi_over_low"
    assert eng.rules()["notes"]["bmi_muscle"] in r["notes"]


def test_limited_evidence_tests_carry_their_limitation():
    r = eng.evaluate({"a2_q3": {"testing_goal": "genetics_nutrition"}}, BRAND)
    assert _services(r) == ["s_dna_test"]
    assert r["services"][0]["product"]["code"] == "hh_dna_kit"
    assert "limited" in r["tests"][0]["limitation"]


def test_missing_fields_stay_unknown():
    r = eng.evaluate({"a1_q1": {"weight_kg": "skipped", "height_cm": 170, "age": "unknown"}}, BRAND)
    assert r["bmi"] is None and r["energy_need_kcal"] is None
    assert r["sections"]["body"]  # height alone counts as an answered section


# ---------- data consistency ----------


def test_every_rule_service_maps_to_a_brand_product_or_referral():
    rules, cat = eng.rules(), eng.catalog(BRAND)
    for service in rules["services"]:
        assert eng._product(service, BRAND), service
    for p in cat["products"]:
        assert set(p["serves"]) <= set(rules["services"]), p["code"]
        assert p["kind"] in ("book", "buy", "subscribe", "info") and p["recommend"] in ("assess", "ask")
    referenced = [t["service"] for t in rules["tests"].values()] + [f["service"] for f in rules["diet"]["flags"].values()]
    referenced += [s["service"] for s in rules["gut"]["symptoms"].values()]
    assert {s for s in referenced if s} <= set(rules["services"])
    for spec in rules["gut"]["symptoms"].values():
        assert set(spec["tests"]) <= set(rules["tests"])


@pytest.mark.parametrize("section", ["bmi", "tests", "services", "diet_plans", "eating_patterns"])
def test_yaml_codes_match_the_tables_doc(section):
    doc = DOC.read_text(encoding="utf-8")
    data = eng.rules()[section]
    codes = [row["code"] for row in data] if isinstance(data, list) else list(data)
    missing = [c for c in codes if not re.search(rf"\b{re.escape(c)}\b", doc)]
    assert not missing, f"in YAML but not in {DOC.name}: {missing}"


# ---------- regressions found by fuzzing and persona review (2026-10-30) ----------


@pytest.mark.parametrize(
    "answers",
    [
        {"a1_q1": {"weight_kg": "180 lb", "height_cm": "5'6", "age": "forty"}},
        {"a1_q1": {"weight_kg": True, "height_cm": [], "age": -5}},
        {"a1_q2": {"activities": ["run_6", 42, None, {"code": ["x"], "intensity": {}, "minutes": "lots"}]}},
        {"a1_q3": {"meals_per_day": "3", "foods": ["pizza", {"kcal_est": "a lot"}], "diet_flags": "sugary_drinks"}},
        {"a2_q2": {"gi_symptoms": [{"code": "dairy"}], "family_history": "fh_celiac"}},
        {"q0": "lose weight", "a1_q1": None, "a2_q3": ["curiosity"]},
        "not a dict",
    ],
)
def test_messy_llm_input_never_crashes_and_becomes_unknown(answers):
    r = eng.evaluate(answers, BRAND)
    assert r["bmi"] is None
    assert len(r["services"]) <= 2


def test_numeric_strings_are_accepted():
    r = eng.evaluate({"a1_q1": {"weight_kg": "70", "height_cm": "175", "age": "30", "sex": "male"},
                      "a1_q2": {"activities": [{"code": "jog", "sessions_per_week": "3", "minutes": "30"}]}}, BRAND)
    assert r["bmi"]["value"] == 22.9
    assert r["exercise"]["weekly_moderate_eq_min"] == 180


def test_age_between_dga_rows_still_has_a_need():
    assert eng.energy_need(age=60.5, sex="female", activity_level="inactive") == (1600, 2000)
    assert eng.energy_need(age=61, sex="female", activity_level="inactive") == (1600, 1600)


def test_routine_labs_go_to_the_partner_lab_not_the_nutrient_subscription():
    r = eng.evaluate({"a1_q1": {"weight_kg": 72, "height_cm": 166, "age": 72, "sex": "female"}}, BRAND)
    labs = next(s for s in r["services"] if "t_hba1c" in s["tests"])
    assert labs["product"]["code"] == "pt_labs"
    vegan = eng.evaluate({"a1_q3": {"meals_per_day": 2, "diet_flags": ["meal_skipping_fatigue"]},
                          "a2_q1": {"diet_plans": ["vegan"]}}, BRAND)
    assert next(s for s in vegan["services"] if "t_nutrients" in s["tests"])["product"]["code"] == "hh_dna_blood"


def test_glp1_users_get_no_medical_weight_referral_and_side_effects_hand_off():
    base = {"q0": {"goal": "lose_weight"}, "a1_q1": {"weight_kg": 98, "height_cm": 165, "age": 48, "sex": "female"}}
    ok = eng.evaluate({**base, "a2_q1": {"diet_plans": ["glp1"], "glp1": "effective"}}, BRAND)
    assert _services(ok)[0] == "s_glp1_program" and "s_medical_weight" not in _services(ok)
    side = eng.evaluate({**base, "a2_q1": {"glp1": "side_effects"}}, BRAND)
    assert side["handoff"] == {"reason": "glp1_side_effects", "urgency": "routine"}
    assert "s_medical_weight" in _services(eng.evaluate(base, BRAND))  # not on GLP-1: referral is fine


def test_low_protein_lifter_gets_sports_nutrition_first():
    r = eng.evaluate({"a1_q2": {"activities": [{"code": "weights_heavy", "sessions_per_week": 4, "minutes": 60}]},
                      "a1_q3": {"meals_per_day": 2, "diet_flags": ["low_protein"]}}, BRAND)
    assert _services(r)[0] == "s_sports_nutrition"


def test_balanced_diet_is_about_right_not_unclear():
    r = eng.evaluate({"a1_q3": {"meals_per_day": 3, "diet_flags": ["balanced"]}}, BRAND)
    assert r["intake"] == "intake_about_right" and r["diet_class"] == "healthy_balanced"
    assert r["case"] == "B"


def test_no_limited_evidence_add_on_next_to_an_indicated_test():
    r = eng.evaluate({"a2_q2": {"gi_symptoms": ["gluten"], "family_history": ["fh_celiac"]},
                      "a2_q3": {"testing_goal": "understand_symptoms"}}, BRAND)
    assert [t["code"] for t in r["tests"]] == ["t_celiac"]
