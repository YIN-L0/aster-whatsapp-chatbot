from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import companion as comp
from app.commands import wipe_user_data
from app.db import session_scope
from app.models import CompanionAssessment, Plan, ProfileFact, ProviderLink, ReferralOffer, User, UserGoal
from app.providers import banned_plan_terms

NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
BODY = {"weight_kg": 80, "height_cm": 170}
MEALS = {"meals_per_day": 3, "diet_flags": ["sugary_drinks"]}
EXERCISE = {"activities": [{"code": "walk_brisk", "sessions_per_week": 3, "minutes": 30}]}


def facts(**slots) -> dict[str, comp.Fact]:
    return {k: comp.Fact(v, NOW) for k, v in slots.items()}


# ---------- slot values ----------


def test_body_measures_convert_imperial_and_reject_implausible_values():
    clean, problems = comp.validate_slot("body_measures", {"weight_lb": 165, "height_ft": 5, "height_in": 6})
    assert clean == {"weight_kg": 74.8, "height_cm": 167.6} and problems == []
    clean, problems = comp.validate_slot("body_measures", {"weight_kg": 70, "height_cm": 1.7})  # metres by mistake
    assert clean == {"weight_kg": 70.0} and problems == ["missing or implausible height_cm"]
    assert not comp.slot_complete("body_measures", clean)


def test_unknown_codes_are_dropped_with_a_problem_never_guessed():
    clean, problems = comp.validate_slot("meals", {"meals_per_day": 3, "diet_flags": ["sugary_drinks", "pizza_lover"]})
    assert clean["diet_flags"] == ["sugary_drinks"] and "pizza_lover" in problems[0]
    clean, problems = comp.validate_slot("exercise", {"activities": [{"code": "parkour", "sessions_per_week": 2}]})
    assert clean["activities"] == [{"code": "other", "sessions_per_week": 2.0}]
    assert "minutes" in problems[0]
    clean, problems = comp.validate_slot("gut_symptoms", {"gi_symptoms": ["tummy"]})
    assert not comp.slot_complete("gut_symptoms", clean) and problems


def test_no_exercise_is_a_real_answer():
    clean, problems = comp.validate_slot("exercise", {"activities": []})
    assert clean == {"activities": []} and problems == [] and comp.slot_complete("exercise", clean)


# ---------- next slot ----------


def test_next_slot_follows_core_order_and_skips_what_is_known_or_skipped():
    assert comp.next_slot("lose_weight", {}, set(), NOW)["slot"] == "body_measures"
    assert comp.next_slot("lose_weight", facts(body_measures=BODY), set(), NOW)["slot"] == "age"
    nxt = comp.next_slot("lose_weight", facts(body_measures=BODY), {"age"}, NOW)
    assert nxt["slot"] == "exercise" and nxt["mode"] == "ask" and nxt["examples"] and nxt["purpose"]


def test_stale_answer_is_confirmed_not_asked_from_scratch():
    f = facts(body_measures=BODY, age={"age": 40})
    f["exercise"] = comp.Fact(EXERCISE, NOW - timedelta(days=31))  # exercise goes stale after 30 days
    nxt = comp.next_slot("lose_weight", f, set(), NOW)
    assert nxt["slot"] == "exercise" and nxt["mode"] == "confirm" and nxt["previous"] == EXERCISE


def test_unstructured_goals_are_chat_only():
    assert comp.next_slot("sleep", {}, set(), NOW) is None
    assert comp.readiness("sleep", {}, set(), NOW)["level"] == "chat_only"
    assert comp.readiness(None, {}, set(), NOW)["level"] == "chat_only"


# ---------- readiness ----------


def test_readiness_depends_on_core_information_not_on_a_count():
    assert comp.readiness("lose_weight", {}, set(), NOW)["level"] == "not_ready"
    # Three supporting answers but nothing core: still not ready
    many = facts(sex={"sex": "female"}, diet_plan={"diet_plans": ["none"]}, gut_symptoms={"gi_symptoms": ["gi_none"]})
    assert comp.readiness("lose_weight", many, set(), NOW)["level"] == "not_ready"
    # Two of four core slots: not yet (body numbers alone say little about habits); three: a first plan is possible
    assert comp.readiness("lose_weight", facts(body_measures=BODY, meals=MEALS), set(), NOW)["level"] == "not_ready"
    three = facts(body_measures=BODY, meals=MEALS, exercise=EXERCISE)
    assert comp.readiness("lose_weight", three, set(), NOW)["level"] == "preliminary"


def test_ready_needs_core_resolved_and_half_of_supporting_and_skips_count_as_resolved():
    core = dict(body_measures=BODY, age={"age": 40}, exercise=EXERCISE, meals=MEALS)
    assert comp.readiness("lose_weight", facts(**core, sex={"sex": "male"}), set(), NOW)["level"] == "preliminary"
    two_sup = dict(sex={"sex": "male"}, diet_plan={"diet_plans": ["none"]})
    assert comp.readiness("lose_weight", facts(**core, **two_sup), set(), NOW)["level"] == "ready"
    without_age = {k: v for k, v in core.items() if k != "age"}
    r = comp.readiness("lose_weight", facts(**without_age, **two_sup), {"age"}, NOW)
    assert r["level"] == "ready" and r["skipped"] == ["age"]


def test_red_flag_and_under_13_block_the_plan_but_special_populations_do_not():
    red = facts(gut_symptoms={"gi_symptoms": ["blood_in_stool"]}, meals=MEALS)
    assert comp.readiness("gut_health", red, set(), NOW)["level"] == "blocked"
    child = facts(body_measures=BODY, age={"age": 12}, meals=MEALS)
    assert comp.readiness("general", child, set(), NOW)["level"] == "blocked"
    teen = facts(body_measures=BODY, age={"age": 16}, meals=MEALS, exercise=EXERCISE)
    assert comp.readiness("general", teen, set(), NOW)["level"] in ("preliminary", "ready")


def test_engine_answers_map_slots_to_steps():
    a = comp.engine_answers("lose_weight", facts(body_measures=BODY, sex={"sex": "prefer_not"}, meals=MEALS))
    assert a["q0"] == {"goal": "lose_weight"}
    assert a["a1_q1"] == {"weight_kg": 80, "height_cm": 170, "sex": None}
    assert a["a1_q3"] == MEALS
    assert comp.engine_answers("sleep", {})["q0"] == {"goal": None}


# ---------- plan ----------


def test_plan_skeleton_has_findings_and_limitations_but_no_services():
    sk = comp.plan_skeleton("lose_weight", facts(body_measures=BODY, meals=MEALS), {"sex"}, NOW)
    assert "services" not in sk and sk["confidence"] == "preliminary"
    assert any(f["area"] == "bmi" for f in sk["findings"])
    assert "Not known yet: age" in sk["limitations"] and "Skipped: sex" in sk["limitations"]
    assert sk["metric_options"]


def test_plan_skeleton_for_a_minor_has_no_bmi_or_calories():
    sk = comp.plan_skeleton("lose_weight", facts(body_measures=BODY, age={"age": 16}, meals=MEALS), set(), NOW)
    assert not any(f["area"] == "bmi" for f in sk["findings"])
    assert sk["energy_need_kcal"] is None and sk["weight_loss_target_kcal"] is None
    assert sk["handoff"]["reason"] == "special_population" and sk["cautions"]


GOOD_PLAN = {
    "situation_summary": "You walk three times a week and have a sweet drink most days.",
    "priorities": ["Swap the daily soda for sparkling water", "Add a fourth walk"],
    "daily_habits": ["Water with every meal"],
    "weekly_habits": ["Plan two home-cooked dinners"],
    "metrics": ["Weight once a week"],
    "review_in_days": 14,
}


def test_validate_plan_accepts_a_good_plan():
    assert comp.validate_plan(GOOD_PLAN, banned_plan_terms()) == []


@pytest.mark.parametrize("change, problem", [
    ({"weekly_habits": ["Book a session at Essence"]}, "providers or products"),
    ({"daily_habits": ["Take HelixVit each morning"]}, "providers or products"),
    ({"priorities": ["Try the $39 coaching session"]}, "prices"),
    ({"priorities": ["a", "b", "c", "d"]}, "priorities"),
    ({"metrics": []}, "metrics"),
    ({"review_in_days": 200}, "review_in_days"),
    ({"situation_summary": ""}, "situation_summary"),
])
def test_validate_plan_rejects_selling_and_malformed_plans(change, problem):
    problems = comp.validate_plan({**GOOD_PLAN, **change}, banned_plan_terms())
    assert any(problem in p for p in problems)


def test_every_goal_slot_exists_and_structured_goals_have_metrics():
    r = comp.rules()
    for code, g in r["goals"].items():
        if g["structured"]:
            assert set(g["core"] + g["supporting"]) <= set(r["slots"]), code
            assert g["metrics"], code
    for slot in r["slots"].values():
        assert slot["label"] and slot["purpose"] and slot["examples"]


# ---------- privacy ----------


async def test_delete_my_data_wipes_companion_tables():
    async with session_scope() as db:
        user = User(wa_id="15550001111")
        db.add(user)
        await db.flush()
        db.add_all([
            ProfileFact(user_id=user.id, key="body_measures", value=BODY),
            UserGoal(user_id=user.id, code="lose_weight", is_focus=True),
            CompanionAssessment(user_id=user.id, goal="lose_weight"),
            Plan(user_id=user.id, goal="lose_weight", confidence="preliminary", content=GOOD_PLAN),
            ReferralOffer(user_id=user.id, need="s_dietitian", status="declined"),
            ProviderLink(user_id=user.id, provider_slug="essence", status="self_reported", source="user_button"),
        ])
        await db.flush()
        await wipe_user_data(db, user)
        for model in (ProfileFact, UserGoal, CompanionAssessment, Plan, ReferralOffer, ProviderLink):
            assert await db.scalar(select(func.count()).select_from(model)) == 0, model.__name__


def test_validate_plan_rejects_a_plan_too_long_for_one_whatsapp_message():
    long_plan = {**GOOD_PLAN, "daily_habits": ["Walk a little more after dinner, most days. " * 10] * 10}
    assert any("too long" in p for p in comp.validate_plan(long_plan, []))
