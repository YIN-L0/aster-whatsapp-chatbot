# Weight, Nutrition & Gut Health Assessment — reference tables (draft v0.1)

Source brief: `Astwe excercise and nutition (1).docx` (client). This file turns the brief into evidence-backed tables
the bot can use. Status: **draft for review — no code yet.** Clinical wording needs sign-off before launch, the same
as `app/protocols/`.

## 0. How the bot uses these tables

- **Code computes, the LLM talks.** BMI, energy needs, exercise volume and table look-ups are done in code, so they are
  fast and repeatable. The LLM only (a) turns the user's free text into the fields in §1 and (b) phrases the result.
- Every field can be `unknown`. Unknown fields never become a guess. Each section is evaluated on its own (brief §F).
- Every table row has a stable `code`, so the YAML version (Phase 2) and the tests can refer to rows by code.
- Evidence grade used in the tables: **A** = guideline / consensus; **B** = good trials or official data;
  **C** = limited or mixed evidence; **X** = not recommended by guidelines (the bot may mention it only as optional /
  exploratory, never as a diagnosis).

## 1. Intake — what each question collects

| Step | Question (brief) | Fields | Purpose |
|---|---|---|---|
| Q0 *(added)* | "What would you most like help with?" | `goal`: lose_weight · build_muscle · maintain · body_shape · gut_health · general | Picks the route (e.g. med spa branch §10 needs `body_shape`) |
| A1-Q1 | Weight, height, age | `weight_kg`, `height_cm`, `age` (exact or band), `sex` *(optional, skippable)* | BMI (§2) and energy needs (§3) |
| A1-Q2 | Exercise, how often | list of `{activity_code, sessions_per_week, minutes}` | Category A–F, weekly volume, kcal burned (§4) |
| A1-Q3 | Meals per day, what you eat and drink | `meals_per_day`, free-text foods → `diet_flags` + rough kcal (§5) | Intake vs need, diet class (§5.4) |
| A2-Q1 | Current diet plan or weight program; does it work | `diet_plan_codes` (§6), `eating_pattern_codes` (§7), `glp1`: none · effective · side_effects | Pros / possible gaps; GLP-1 routing |
| A2-Q2 | GI problems or family history (private) | `gi_symptom_codes` (§8), `family_history_codes` | Gut route; red flags; testing eligibility |
| A2-Q3 | What do you want from gut testing | `testing_goal`: understand_symptoms · genetics_nutrition · inherited_risk · curiosity · none | Choose 0–2 tests (§9) |

Units: accept lb / ft-in and convert in code. **Under 18, pregnant or breastfeeding** (client decision): skip the BMI
category and calorie targets (adult cut-offs and DGA estimates don't apply), give general guidance only, and send a
routine handoff to the brand's care team. The conversation stays in AI mode.

## 2. BMI (A1-Q1)

`BMI = weight_kg / (height_m)²`, rounded to 1 decimal.

| Code | Category | BMI | Aster interpretation |
|---|---|---|---|
| bmi_severe_under | Severely underweight | < 16.0 | Significantly below healthy range |
| bmi_under | Underweight | 16.0–18.4 | Below healthy range |
| bmi_healthy | Healthy weight | 18.5–24.9 | Standard healthy BMI range |
| bmi_over_low | Slightly overweight | 25.0–27.4 | Lower part of the overweight range |
| bmi_over_high | Overweight | 27.5–29.9 | Upper part of the overweight range |
| bmi_ob1 | Obesity class I | 30.0–34.9 | Obesity |
| bmi_ob2 | Obesity class II | 35.0–39.9 | Severe obesity |
| bmi_ob3 | Obesity class III | ≥ 40.0 | Severe obesity |

Grade A (WHO classes). The 27.5 split matches WHO's extra "public health action points" (23, 27.5, 32.5, 37.5).

Caveats the bot adds when relevant:
- **Strength-trained users** (category D/E, ≥3 sessions/week) with BMI 25–29.9: "BMI doesn't tell muscle from fat."
- **Asian ancestry**: risk starts at lower BMI. ADA screens Asian Americans from BMI ≥ 23.
- **Age ≥ 65**: a BMI slightly above 25 is not by itself a concern. Avoid weight-loss framing.
- The bot never uses "obese" about the user. It says "in the obesity range" only when they asked for their category.

## 3. Daily energy needs (A1-Q3 comparison)

### 3.1 Formula (primary)
Mifflin-St Jeor resting energy (Grade B, the most accurate of the common equations in adults):

- Men: `10·kg + 6.25·cm − 5·age + 5`
- Women: `10·kg + 6.25·cm − 5·age − 161`
- Sex unknown: compute both and use the range.

`Daily need = resting × activity factor` (factor from §4.3):

| Activity level (from §4.3) | Factor |
|---|---|
| inactive | 1.2 |
| low | 1.375 |
| meets_guideline | 1.55 |
| high | 1.725 |

Age as a band only: use the band midpoint. Weight-loss target, if the user wants one: need − 300 to 500 kcal. Never
suggest below 1,200 (women) / 1,500 (men) kcal/day, and never give a target if BMI < 18.5.

### 3.2 Fallback ranges (no height or weight) — DGA 2020–2025, Appendix 2

| Age | Women, sedentary | Women, active | Men, sedentary | Men, active |
|---|---|---|---|---|
| 19–60 | 1,600–2,000 | 2,200–2,400 | 2,200–2,600 | 2,600–3,000 |
| 61+ | 1,600 | 2,000 | 2,000 | 2,400–2,600 |

Grade A. Excludes pregnancy and lactation.

## 4. Exercise (A1-Q2)

### 4.1 Activity → MET → kcal
`kcal ≈ MET × weight_kg × hours` (gross; includes resting burn). METs are from the **2024 Adult Compendium of
Physical Activities** (Grade B, adults 19–59). The kcal column is for 70 kg and 30 minutes.

| Code | Activity | MET | kcal (70 kg, 30 min) | Category |
|---|---|---|---|---|
| walk_slow | Walking 2.5 mph | 3.0 | 105 | B |
| walk_moderate | Walking 2.8–3.4 mph | 3.8 | 133 | B |
| walk_brisk | Walking 3.5–3.9 mph, for exercise | 4.8 | 168 | B/C |
| yoga_hatha | Yoga, hatha / general | 2.3 | 81 | B |
| yoga_power | Yoga, power | 4.0 | 140 | B |
| yoga_vinyasa | Yoga, vinyasa | 2.7 | 95 | B |
| pilates | Pilates, general | 2.8 | 98 | F |
| stretching | Stretching, mild | 2.3 | 81 | B |
| gardening | Gardening, moderate | 3.8 | 133 | B |
| cycle_leisure | Cycling 10–11.9 mph | 6.8 | 238 | C |
| cycle_moderate | Cycling 12–13.9 mph | 8.0 | 280 | C |
| cycle_stationary | Stationary bike 101–125 W | 6.8 | 238 | C |
| jog | Jogging, general | 7.5 | 263 | C |
| run_5 | Running 5 mph (12 min/mile) | 8.5 | 298 | C |
| run_6 | Running 6 mph (10 min/mile) | 9.3 | 326 | C |
| run_7 | Running 7 mph | 11.0 | 385 | C |
| swim_slow | Swimming laps, slow | 5.8 | 203 | C |
| swim_fast | Swimming laps, fast | 9.8 | 343 | C |
| elliptical | Elliptical, moderate | 5.0 | 175 | C |
| rowing | Rowing machine, moderate | 5.0 | 175 | C |
| hiit | HIIT, moderate | 7.0 | 245 | C |
| hiit_vig | HIIT, vigorous (burpees, Tabata) | 11.0 | 385 | C |
| zumba | Zumba / aerobic dance class | 6.5 | 228 | C |
| weights | Resistance training, 8–15 reps | 3.5 | 123 | D |
| weights_heavy | Squats / deadlifts | 5.0 | 175 | D |
| weights_vig | Bodybuilding / power lifting, vigorous | 6.0 | 210 | D |
| bodyweight | Body-weight exercises, general | 3.0 | 105 | D |
| circuit | Circuit training, moderate | 5.0 | 175 | E |
| circuit_vig | Circuit / kettlebell, vigorous | 7.5 | 263 | E |
| tennis_doubles | Tennis, doubles | 6.0 | 210 | F |
| tennis_singles | Tennis, singles | 8.0 | 280 | F |
| basketball | Basketball, game | 8.0 | 280 | F |
| soccer | Soccer, casual | 7.0 | 245 | F |
| hiking | Hiking | 6.0 | 210 | F |
| golf | Golf, walking | 4.5 | 158 | F |
| other | Anything else | ask intensity: light 2.5 · moderate 4.5 · vigorous 7.0 | — | F |

A user doing both cardio (C) and strength (D) counts as **E** (balanced).

### 4.2 Categories (brief subsheet A1-Q2)

| Cat | Type | Calorie burn | Muscle building | Weight-loss assessment | Typical next step |
|---|---|---|---|---|---|
| A | No exercise | Very low | Very low | Little help from exercise | Start with 10-min walks, build to 150 min/week |
| B | Light (walking, gentle yoga) | Low–moderate | Low | Good start | Raise intensity or time; add 2 strength days |
| C | Cardio | Moderate–high | Low–moderate | Strong for burn | Add 2 strength days to protect muscle |
| D | Strength | Moderate | High | Keeps muscle; less burn | Add aerobic minutes if the goal is weight loss |
| E | Cardio + strength | Moderate–high | High | Best overall | Keep it; check protein and recovery |
| F | Sports / other | Varies | Varies | Depends on intensity | Check weekly minutes |

### 4.3 Weekly volume → activity level
Weekly moderate-equivalent minutes = moderate minutes (MET 3.0–5.9) + 2 × vigorous minutes (MET ≥ 6). Activities with
MET < 3 count as light.

| Level | Moderate-equivalent min/week | Meaning |
|---|---|---|
| inactive | 0, light activity only, or < 30 | Below the guideline |
| low | 30–149 | Some activity, below the guideline |
| meets_guideline | 150–300 | Meets the PAG 2018 aerobic guideline |
| high | > 300 | Extra health benefit |

Reference points (Grade A/B):
- **PAG 2018:** 150–300 min moderate or 75–150 min vigorous aerobic activity per week, plus muscle-strengthening on
  ≥ 2 days.
- **ACSM 2009 (weight):** 150–250 min/week gives modest weight loss; > 250 min/week is linked to clinically
  significant loss; 200–300 min/week helps keep weight off after loss.
- **On GLP-1:** strength training ≥ 3×/week plus ≥ 150 min aerobic activity (2025 joint advisory).

## 5. Diet (A1-Q3) — no calorie counting by the user

### 5.1 Daily nutrient targets (adults)

| Nutrient | Target | Source / grade |
|---|---|---|
| Protein, general | 0.8 g/kg/day | RDA, A |
| Protein, during weight loss | 1.2–1.6 g/kg/day (or 80–120 g/day); avoid ≥ 2 g/kg | 2025 GLP-1 joint advisory, A |
| Protein, regular exercisers | 1.4–2.0 g/kg/day; 20–40 g per meal | ISSN 2017, B |
| Fiber | 14 g per 1,000 kcal (about 25 g women, 31–38 g men) | IOM DRI / DGA, A |
| Added sugars | < 10% of calories (< 50 g at 2,000 kcal) | DGA, A |
| Saturated fat | < 10% of calories | DGA, A |
| Sodium | < 2,300 mg/day | DGA, A |
| Vegetables / fruit | 2.5 cups veg + 2 cups fruit at 2,000 kcal | DGA, A |
| Alcohol | If at all: ≤ 1 drink/day women, ≤ 2 men | DGA, A |
| Water (all sources) | About 2.7 L/day women, 3.7 L/day men | IOM, A |

### 5.2 Quick energy estimates for common foods (LLM extraction aid)
Approximate values in USDA FoodData Central range (Grade B). Used only to place intake in a band, never quoted to
the user as an exact number.

| Item | Typical portion | kcal | Note |
|---|---|---|---|
| Regular soda / sweet tea | 12 oz | 150 | ~39 g sugar |
| Sweetened latte / frappé | 16 oz | 250–400 | |
| Juice | 8 oz | 110 | |
| Beer | 12 oz | 150 | |
| Wine | 5 oz | 120 | |
| Fast-food burger + fries + soda | 1 meal | 1,100–1,300 | |
| Pizza | 1 slice | 285 | |
| Restaurant pasta | 1 plate | 800–1,200 | |
| Burrito / burrito bowl | 1 | 700–1,100 | |
| Sandwich / sub | 1 | 400–800 | |
| Salad with dressing and protein | 1 bowl | 350–600 | |
| Rice or pasta, cooked | 1 cup | 200 | |
| Bread | 1 slice | 80 | |
| Chicken breast / fish | palm size (~4 oz) | 150–200 | ~30 g protein |
| Steak | 6 oz | 400 | |
| Eggs | 2 | 140 | 12 g protein |
| Greek yogurt, plain | 1 cup | 130 | 20 g protein |
| Oatmeal | 1 cup cooked | 160 | 4 g fiber |
| Cereal with milk | 1 bowl | 250–350 | |
| Pastry / donut / muffin | 1 | 250–450 | |
| Chips | 1 oz bag | 150 | |
| Nuts | handful (1 oz) | 170 | |
| Chocolate bar | 1 | 220–250 | |
| Fruit (apple, banana) | 1 | 95–105 | 3–4 g fiber |
| Protein shake | 1 | 150–250 | 20–30 g protein |

### 5.3 Intake vs need
Self-reported intake is usually **under-reported**, often by 10–30% or more. So the bot only uses wide bands:

| Code | Rule | What the bot says |
|---|---|---|
| intake_likely_above | estimate > need × 1.15, **or** ≥ 2 high-energy flags (§5.4) | "This suggests you may be eating more than your body needs" |
| intake_about_right | within ± 15% and fewer than 2 high-energy flags (a single flag still shows up as its own finding) | "Your intake sounds roughly in line with your needs" |
| intake_likely_below | estimate < need × 0.7, or skipping meals with fatigue | "You may not be eating enough to fuel your day" |
| intake_unclear | not enough detail | Ask about portions or typical foods once, if the user agrees |

### 5.4 Diet findings → recommendation → service (brief "Findings" table, completed)

| Code | Trigger (from the user's description) | Potential issue | Recommendation | Service |
|---|---|---|---|---|
| sugary_drinks | ≥ 1 sugary drink/day | Extra added sugar and energy | Swap some for water, sparkling water or unsweetened drinks | Nutrition coaching |
| large_portions | Restaurant / fast food ≥ 3×/week, or "big plates" | Possible excess energy | Review portions; half-plate vegetables | Dietitian |
| low_plants | < 2 servings of fruit or vegetables a day | Low fiber and micronutrients | Add one fruit or vegetable to each meal, gradually | Nutrition coaching |
| low_protein | Protein in fewer than 2 meals | Possible low protein (worse with weight loss or GLP-1) | Protein at each meal (palm-size portion) | Dietitian if needed |
| meal_skipping_fatigue | Skips meals and reports tiredness | Possible inadequate intake | Review total intake and symptoms | Dietitian / clinician |
| restrictive | Cuts whole food groups, very low intake, or < 1,200 kcal | Risk of nutrient gaps | Check adequacy before cutting more | Registered dietitian |
| limited_variety | Same few foods daily | Possible nutrient gaps | Diversify gradually | Nutrition coaching |
| frequent_alcohol | Above the DGA limits | Extra energy; sleep and liver effects | Cut back; alcohol-free days | Nutrition coaching |
| late_night | Most calories after 8 pm or night snacking | Often extra energy; poorer sleep | Earlier, bigger breakfast or lunch | Nutrition coaching |
| balanced | Varied meals, protein and plants at most meals | No obvious issue | Keep sustainable habits | Optional coaching |
| insufficient_detail | Too vague | Can't evaluate | Ask once about portions or typical foods | General education |

**Diet class (brief "Primary Assessment"):**

| Class | Rule |
|---|---|
| healthy_balanced | `balanced` and no other flags, and intake not `likely_above` |
| needs_improvement | 1–3 of: sugary_drinks, large_portions, low_plants, low_protein, limited_variety, frequent_alcohol, late_night; or `intake_likely_above` |
| potentially_unhealthy | `restrictive`, `meal_skipping_fatigue`, ≥ 4 flags, or `intake_likely_below` with BMI < 18.5 |

## 6. Common diet plans (A2-Q1, subsheet "Common Dietary Plans")

| Code | Plan | Evidence for weight | Strengths | Possible nutritional downsides | Aster tip |
|---|---|---|---|---|---|
| keto | Ketogenic (< 50 g carbs) | B: fast early loss; similar to other diets at 12 months | Appetite control | Low fiber; low fruit, whole grains and potassium; LDL can rise | Choose non-starchy veg and unsaturated fats; check lipids with the clinician |
| low_carb | Low-carb (not keto) | A/B: ≈ low-fat at 12 months (DIETFITS) | Fewer refined carbs | Low fiber if veg is low | Keep veg, legumes, nuts |
| low_fat | Low-fat | A/B: ≈ low-carb (DIETFITS) | Heart-friendly if unprocessed | Refined "low-fat" products can be sugary | Watch added sugar |
| intermittent_fasting | 16:8, 5:2, time-restricted eating | B: similar to daily calorie restriction | Simple structure | Low protein if the eating window is short; overeating in the window | Protein at the first meal |
| mediterranean | Mediterranean | A: strong heart benefit (PREDIMED); moderate weight effect | Fiber, healthy fats | Portions of oil and nuts are calorie-dense | Mind portions |
| dash | DASH | A: lowers blood pressure | Fruit, veg, low sodium | Few | Good default pattern |
| vegetarian | Vegetarian | B | Fiber | Iron, B12, omega-3 | Clinician can check B12 / iron if symptoms appear |
| vegan | Vegan / plant-based | B | Fiber, low saturated fat | B12 (needs a reliable source), iron, calcium, vitamin D, iodine, omega-3, protein | Mention B12; offer a dietitian review |
| high_protein | High-protein | B: helps fullness and muscle retention | Satiety | Low fiber if plants are low; avoid ≥ 2 g/kg | Pair with plants |
| meal_replacement | Shakes / bars | A/B: effective in structured programs (Look AHEAD, DiRECT) | Easy control | Hard to sustain; low variety | Plan the transition back to food |
| commercial_program | Points / calorie apps / programs | B | Accountability | Depends on the program | Keep what works |
| carnivore | Meat-only | C: very little evidence | — | No fiber, vitamin C or plant nutrients; high saturated fat | Suggest a dietitian review |
| cleanse | Juice or detox cleanse | X: no evidence for lasting loss | — | Very low protein; rebound | Gently suggest a sustainable plan |
| glp1 | GLP-1 medication (semaglutide, tirzepatide) | A: large effect under a clinician | Appetite control | Muscle loss; low protein; low fluid; micronutrient gaps; GI side effects | Protein, strength training, fluids; side effects → prescribing clinician |
| none | No plan | — | — | — | General guidance |

The bot never comments on medication choice or dose (existing safety rule).

## 7. Eating patterns (A2-Q1, subsheet "Eating Patterns")

| Code | Pattern | Possible effect | Aster suggestion | Service |
|---|---|---|---|---|
| regular | 3 regular meals ± 1 snack | Neutral or positive | Keep it | — |
| skip_breakfast | Skips breakfast | Neutral for some; others overeat later | If hungry later, try a protein breakfast | Coaching optional |
| irregular | Irregular meal times | Harder appetite control | Set rough meal times | Nutrition coaching |
| grazing | Frequent snacking / grazing | Hidden energy | Planned snacks | Nutrition coaching |
| late_eating | Large late meals | Extra energy; reflux; poorer sleep | Shift energy earlier | Nutrition coaching |
| emotional | Stress or emotional eating | Overeating episodes | Notice triggers; non-food coping | Coaching / clinician |
| eat_out | Eats out or delivery ≥ 4×/week | Larger portions, more sodium | Pick lighter options; share or box half | Nutrition coaching |
| binge_like | Loss-of-control episodes | Possible disordered eating | Kind, non-judgmental reply; no restriction advice; suggest a clinician or dietitian | Clinician / RD |
| very_low | Very little food overall | Nutrient gaps; low energy | Don't advise cutting more | Registered dietitian |

**Restrictive or loss-of-control eating** (codes `restrictive`, `very_low`, `binge_like`, or BMI < 18.5 with a
weight-loss goal) — client decision: send a routine handoff to the brand's care team and keep the conversation going
in AI mode. The bot keeps asking the remaining questions, but never recommends weight-loss services or calorie
reduction for these users. It gently says a dietitian or clinician can help and that the team will reach out.

## 8. GI symptoms → action (A2-Q2, subsheet "GI issue and potential recommendation")

**Red flags first** (ACG 2021 alarm features + common practice). Any of these → fixed safety reply + staff handoff; no
test sales:
blood in stool or black stool · unintentional weight loss · persistent vomiting · severe or worsening abdominal
pain · trouble swallowing · fever with diarrhea · new bowel changes at age ≥ 45 with no colon screening · night-time
diarrhea that wakes you · known iron-deficiency anaemia.

| Code | User reports | Possible test | Home collection? | Specialist | Aster action |
|---|---|---|---|---|---|
| gi_none | No symptoms | None routinely | — | Not needed | General nutrition guidance |
| bloat_mild | Occasional mild bloating | Usually none | — | Dietitian if desired | Food and symptom diary for 2 weeks |
| bloat_frequent | Frequent bloating | Targeted: celiac serology or lactose breath test, depending on history | Some options | Primary care / GI if persistent | Offer evaluation and booking |
| dairy | Symptoms after dairy | Lactose hydrogen breath test (positive: rise > 20 ppm after 50 g lactose) | Some supervised kits | Primary care / GI if unclear | Explain options; arrange testing |
| gluten | Symptoms after gluten | Celiac serology: tTG-IgA + total IgA, **while still eating gluten** | Some home blood draws | Clinician to interpret | Test **before** going gluten-free |
| diarrhea_persistent | Diarrhea > 4 weeks | Clinician-selected stool / blood tests; fecal calprotectin; celiac serology | Some stool tests | Primary care / GI | Clinician assessment first |
| constipation_persistent | Constipation > 3 months | History and medication review first | No routine home test | Primary care / GI if persistent | Fiber and fluid review; symptom diary; booking |
| inflammation | Possible IBD (diarrhea, pain, family history of IBD) | Fecal calprotectin | Yes | GI / primary care | Arrange clinician-directed test |
| reflux | Heartburn ≥ 2×/week | None at first | — | Primary care if persistent or with alarm signs | Meal timing and size tips; booking |
| glp1_gi | GI side effects on GLP-1 | — | — | Prescribing clinician | Small frequent meals, fluids; tell the clinician |
| red_flag | Any red flag | Urgent clinical evaluation | Not a first step | Prompt medical care | Escalate; never sell a home test |

**Family history codes** (stored only, never shared): `fh_celiac` (first-degree relative → celiac serology is
reasonable) · `fh_ibd` (alarm feature) · `fh_colon_cancer` (earlier colon screening; clinician) · `fh_high_cholesterol`
or `fh_early_heart_disease` (§9 FH row) · `fh_diabetes` (§9 HbA1c row).

## 9. Testing (A2-Q3, subsheet "Genetic & Gut Health Testing")

| Code | User situation | Test | What it shows | Evidence | Aster recommendation |
|---|---|---|---|---|---|
| t_nutrigenetic | Wants genetic insight into nutrition | Nutrigenetic DNA test (saliva) | Selected variants linked to nutrient metabolism and food responses | C: trials (e.g. DIETFITS, Food4Me) show no extra weight loss from genotype-matched diets; may help motivation | Optional wellness test; say that evidence for better weight loss is limited |
| t_metabolic_panel | Interested in inherited metabolic traits | Metabolic genetic panel | Specific inherited conditions | A only with personal or family history | Only when history supports it |
| t_fh | Family history of very high cholesterol or early heart disease, or LDL ≥ 190 | FH genetic test | Inherited high cholesterol | A: FH Foundation 2018 / AHA (LDL ≥ 190 + family history; ≥ 250 regardless) | Clinical evaluation and genetic counseling |
| t_microbiome | Persistent bloating or irregular bowels; curiosity | Gut microbiome stool test | Microbial composition and diversity | X: 2025 international consensus — not for routine clinical use; direct-to-consumer requests discouraged | Optional exploratory test, **not** a diagnosis for bloating or IBS |
| t_lactose | Dairy symptoms | Lactose breath test | Lactose malabsorption | A/B | Clinician-directed if symptoms warrant |
| t_celiac | Gluten symptoms, IBS with diarrhea, first-degree relative with celiac | tTG-IgA + total IgA | Celiac antibodies | A: ACG 2023 | Clinical test **before** a gluten-free diet |
| t_calprotectin | Persistent diarrhea or inflammatory symptoms | Fecal calprotectin | Intestinal inflammation | A: ACG 2021 | Clinician-directed |
| t_nutrients | Possible deficiency (vegan, restrictive diet, GLP-1, fatigue) | Targeted blood tests (iron/ferritin, B12, vitamin D…) | Specific deficiencies | A when a risk is present | Based on diet risks and symptoms |
| t_hba1c | Weight concerns + diabetes risk (BMI ≥ 25, or ≥ 23 if Asian, + a risk factor; or any adult ≥ 35) | HbA1c | 3-month average glucose (prediabetes 5.7–6.4%; diabetes ≥ 6.5%) | A: ADA | Evidence-based screening; repeat every 3 years if normal |
| t_lipids | Weight concerns + heart risk factors | Lipid panel | Cholesterol, triglycerides | A | Routine risk-based screening |
| t_food_sensitivity | Asks about food sensitivity (IgG) tests | IgG food panel | IgG antibodies (show exposure, not intolerance) | X: ACG 2021 does not recommend it | Explain the limits; suggest a symptom diary or a dietitian instead |

Choice rule: from the user's `testing_goal` and findings, pick **at most 2** tests, A-graded rows first. If
`testing_goal = none`, skip testing.

## 10. Services

Generic service codes used by the rules. Each brand maps them to its own products. The demo nutrition client is
the fictional **Helix & Harvest** — products, prices and mapping in
[DEMO_CLIENT_HELIX_HARVEST.md](DEMO_CLIENT_HELIX_HARVEST.md).

| Code | Service | Demo price (Helix & Harvest) | Use when |
|---|---|---|---|
| s_dna_test | DNA nutrition test (kit; nutrient targets, food scores, food-response insights) | hh_dna_kit $179 | `t_nutrigenetic`; wants a personal food guide |
| s_dna_coaching | Nutrition coaching with a registered dietitian (optionally with the DNA kit) | hh_coach_membership $99/month; hh_kit_coaching $149 + $99/month | Needs improvement + wants support; multiple concerns |
| s_dna_upload | Report from existing ancestry-test DNA data | hh_dna_upload $129 | Already has raw DNA data |
| s_programs | Goal programs: weight loss, menopause, healthy aging | Included in coaching | Matching goal |
| s_glp1_program | GLP-1 support program (protein, digestion, muscle, habits) | hh_glp1_program $129 (4 months) | `glp1 = effective` or `side_effects` (with clinician follow-up) |
| s_dietitian | Registered dietitian session | hh_coach_review $39.95 / coaching | Restrictive, very low intake, binge-like eating, gut-symptom diet plan, `potentially_unhealthy` |
| s_fitness | Fitness coaching — resistance training / cardio / mobility | Partner (mock provider) | Category A/B, or C without strength |
| s_sports_nutrition | Sports nutrition coaching | Partner (mock provider) | Category D/E, `low_protein` |
| s_gut_eval | Gut health evaluation (primary care / GI) | Partner (mock provider) | Persistent GI rows in §8 |
| s_labs | Lab tests (HbA1c, lipids, nutrients, celiac) | hh_dna_blood $99/month for nutrients; others partner | §9 A-graded rows |
| s_genetic_counsel | Genetic counseling | Partner (mock provider) | `t_fh`, `t_metabolic_panel` |
| s_medspa | Med spa consultation (body contouring, skin tightening) | Referral to Essence | §12 branch only |
| s_medical_weight | Medical weight management consult | Essence | BMI ≥ 30, or ≥ 27 with a risk factor, and the user wants weight loss |

**Supplements and nutrition products** (client decision): the user can ask about them and order them, but the
rules never recommend one from findings, and the bot never gives doses or says a product treats a symptom.

**Limited-evidence items** (grade C/X, e.g. the DNA diet test, microbiome tests): may be recommended when the user's
goal matches, always with the fixed sentence from the YAML ("optional; evidence that it improves weight loss is
limited; not a diagnostic test").

## 11. Final recommendation logic (brief §"Final Recommendation Logic", completed)

Order of priorities: 1 safety → 2 nutrition → 3 exercise → 4 testing / coaching / referral → 5 booking. **At most 2
services** per summary.

| Code | Finding | Primary recommendation | Additional service |
|---|---|---|---|
| r_excess | intake_likely_above | s_dna_coaching (nutrition coaching) | s_fitness if activity is inactive or low |
| r_low_protein_strength | low_protein + category D/E | Protein review | s_sports_nutrition |
| r_strength_recovery | category D/E, nutrition not answered (brief §A example) | Recovery nutrition | s_sports_nutrition |
| r_low_fiber_constip | low_plants + constipation | Fiber review | s_gut_eval if it persists |
| r_bloating | any persistent gut row (§8) | s_gut_eval — symptom tests (celiac, lactose, calprotectin) are arranged inside this evaluation | s_dna_coaching afterwards |
| r_inherited | fh_* or t_fh / t_metabolic_panel | Clinical screening (s_labs) | s_genetic_counsel if indicated |
| r_inactive | activity inactive or low | Gradual activity plan | s_fitness |
| r_cardio_only | category C, no strength | Add resistance training | s_fitness |
| r_glp1_ok | glp1 = effective | Keep the provider's plan | s_glp1_program |
| r_glp1_side | glp1 = side_effects | Follow up with the prescribing clinician | s_dietitian |
| r_multi | ≥ 2 diet flags + inactive or low | Joint nutrition + fitness plan | s_dna_coaching |
| r_restrictive | restrictive / very_low / binge_like | s_dietitian | No weight-loss services |
| r_none | No info | General guidance | Optional assessment |

Partial information (brief §A–G), summarized:

| Case | Has | Gives |
|---|---|---|
| A | Exercise only | Exercise plan + general nutrition + offer diet questions |
| B | Diet only | Diet plan + general exercise + optional fitness referral |
| C | Diet concern or unsure | Dietitian / structured assessment + nutrition coaching |
| D | Gut symptoms | Gut evaluation + indicated test or specialist + nutrition follow-up |
| E | Everything | Integrated plan; tests and referrals **only where findings justify them** |
| F | Some sections | Evaluate each section alone; unknowns stay unknown |
| G | Nothing / declined | Standard reply (brief §G) + browse services + booking |

## 12. Med spa branch (brief §"Med spa specific")

Trigger: `goal = body_shape` **and** BMI 18.5–24.9 (gym attendance doesn't matter; underweight users get the nutrition route instead). Recommend only a **consultation**,
never a specific treatment.

| User | Aster action |
|---|---|
| Healthy or stable weight, wants a different body shape | Explore non-invasive body contouring → consult with a med spa partner |
| Close to goal, localized fat | Body contouring consultation |
| Exercises and eats well, wants aesthetic changes | Explain the options; refer to qualified providers |
| Loose skin after weight loss | Skin-tightening consultation |
| BMI ≥ 25 and wants weight loss | Nutrition, exercise and medical weight evaluation first; med spa only for a separate aesthetic goal |
| On GLP-1, worried about body changes | Ask about aesthetic goals; consultation alongside ongoing medical care |
| No info | General information; browse providers without an assessment |

## 13. Safety rules specific to this flow

- Needs `health_consent`. Weight, gut and family-history data are stored only and never shared (client decision).
- Red flags (§8) → existing emergency / handoff path.
- No diagnosis, medication or dose advice. Supplements only on request (§10). Tests are "may be worth discussing",
  never "you have X".
- Grade X items only as optional or exploratory, with their limits stated.
- BMI < 18.5 or restrictive eating → no calorie-deficit or weight-loss services + routine handoff (§7).
- Under 18 / pregnant / breastfeeding → no BMI category or calorie target + routine handoff (§1).
- At most 2 services; med spa only through §12.

## Sources

- WHO BMI classification and public-health action points (WHO Expert Consultation, *Lancet* 2004).
- [Dietary Guidelines for Americans 2020–2025, Appendix 2 (via Merck Manual)](https://www.merckmanuals.com/home/multimedia/table/estimated-number-of-calories-needed-based-on-age-sex-and-activity-level) · DietaryGuidelines.gov.
- Mifflin MD et al., *Am J Clin Nutr* 1990 (resting energy equation).
- [2024 Adult Compendium of Physical Activities (PubMed)](https://pubmed.ncbi.nlm.nih.gov/38242596/) · [tracking guide PDF](https://pacompendium.com/wp-content/uploads/2025/02/4_2024_adult-compendium-tracking-guide-1-2024.pdf).
- Physical Activity Guidelines for Americans, 2nd ed. (HHS 2018) · [CDC adult fact sheet](https://www.cdc.gov/physicalactivity/downloads/PA_Fact_Sheet_Adults.pdf).
- Donnelly JE et al., ACSM Position Stand on physical activity for weight loss, *Med Sci Sports Exerc* 2009.
- [ISSN Position Stand: protein and exercise (2017)](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5477153/).
- [Nutritional Priorities to Support GLP-1 Therapy for Obesity (ACLM/ASN/OMA/TOS 2025)](https://pmc.ncbi.nlm.nih.gov/articles/PMC12125019/).
- [ACG Clinical Guideline: IBS (Lacy et al. 2021)](https://nursingcenter.com/clinical-resources/guideline-summaries/irritable-bowel-syndrome-acg-clinical-guideline-on).
- [ACG Guideline: Celiac Disease (2023), AAFP summary](https://www.aafp.org/afp/2024/0100/practice-guidelines-celiac-disease).
- [International consensus statement on microbiome testing in clinical practice, *Lancet Gastroenterol Hepatol* 2025](https://www.gutmicrobiotaforhealth.com/new-international-consensus-on-microbiome-testing-in-clinical-practice/).
- [ADA screening for prediabetes / diabetes](https://www.hcplive.com/view/guidelines-increase-screening-eligibility-prediabetes-diabetes).
- [FH Foundation consensus on FH genetic testing (2018)](https://cardiovascularbusiness.com/topics/clinical/heart-health/experts-provide-recommendations-genetic-testing-familial).
- [Nutrigenetic diet vs standard diet RCT](https://pmc.ncbi.nlm.nih.gov/articles/PMC4546861) · Gardner CD et al., DIETFITS, *JAMA* 2018.
- USDA FoodData Central (food energy estimates).
