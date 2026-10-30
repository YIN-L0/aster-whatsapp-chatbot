# Demo client: Helix & Harvest Nutrition (fictional)

**Helix & Harvest is a made-up brand for the demo.** Its name, contact details, links and product names are
invented. The product structure and prices are modelled on a real DNA-nutrition company's public catalog (checked
2026-10-29), so the demo feels realistic. Product descriptions are rewritten in our own words. Nothing here may be
presented as a real company.

## 1. Brand facts (for the bot's BRAND FACTS block)

| Field | Value |
|---|---|
| Name | Helix & Harvest Nutrition |
| What it is | DNA-based personalized nutrition: at-home DNA test, nutrition reports, coaching with registered dietitians, programs (weight loss, GLP-1 support, menopause, healthy aging), personalized vitamins and nutrition products |
| Website | https://www.helixharvest.example |
| Shop / checkout (demo, fake) | https://shop.helixharvest.example |
| Email | hello@helixharvest.example |
| Phone | (414) 555-0187 |
| Hours | Mon–Fri 9 am–5 pm Central (coaching by appointment) |
| Location | Online only; kits shipped in the US |
| Lab and privacy claims (demo text) | Samples processed by a CLIA-certified lab in the US; data encrypted and never sold |
| Required disclaimer | "These statements have not been evaluated by the FDA. Our products are not intended to diagnose, treat, cure or prevent any disease." |
| Payments | HSA/FSA may apply with a letter of medical necessity (demo text) |
| Bot persona | "Aster, the Helix & Harvest nutrition assistant" |
| Staff for handoffs | Helix & Harvest care team (separate WhatsApp numbers from Essence) |

## 2. How a customer journey works

1. Order a DNA kit (cheek swab / saliva), or upload raw data from an existing ancestry test.
2. Mail the sample back; results appear in about 4–6 weeks (demo text) in the app and dashboard.
3. Reports: daily macro and micronutrient targets, scores for 300+ foods, and food-response insights (gluten,
   lactose, caffeine, alcohol), plus eating-behavior and stress insights.
4. Optional: a blood nutrient panel, a coaching membership, a program, personalized vitamins.
5. Coaching: bi-weekly 1:1 video sessions, 5-day meal plans, progress tracking.

## 3. Catalog

`kind`: **book** = appointment (booking tools) · **buy** = one-time purchase (fake checkout link) ·
**subscribe** = recurring membership (fake checkout link) · **info** = describe only.

`recommend`: **assess** = the assessment engine may recommend it when findings match · **ask** = only when the user
asks or picks it (supplements and nutrition products — client decision) · **no** = never proactively.

### 3.1 Tests and reports

| Code | Product | Price | Kind | Recommend | What it is |
|---|---|---|---|---|---|
| hh_dna_kit | DNA Nutrition Report + Test Kit | $179 | buy | assess | At-home DNA kit + core report: nutrient targets, food scores, food-response insights |
| hh_dna_upload | DNA Nutrition Report from existing data | $129 | buy | assess | Same report from an existing ancestry test file |
| hh_rpt_weight | Healthy Weight Insights Report | $69 | buy | assess | Add-on report: genetic traits linked to weight and appetite |
| hh_rpt_glucose | Blood Glucose Insights Report | $49 | buy | assess | Add-on report: traits linked to blood sugar response |
| hh_rpt_heart | Heart Smart Nutrition Report | $49 | buy | assess | Add-on report: traits linked to cholesterol and heart-healthy eating |
| hh_rpt_sensitivity | Sensitivity & Metabolism Report | $39 | buy | assess | Add-on report: gluten, lactose, caffeine and alcohol response traits |
| hh_rpt_eating | Eating Insights Report (app only) | $29.95 | buy | ask | Cravings, snacking and meal-timing tendencies |
| hh_rpt_stress | Stress Insights Report (app only) | $19.95 | buy | ask | Stress and sleep-related traits |
| hh_rpt_supplement | Personalized Supplement Report | $29 | buy | ask | Which nutrients the report suggests attention to |

### 3.2 Coaching, programs, memberships

| Code | Product | Price | Kind | Recommend | What it is |
|---|---|---|---|---|---|
| hh_coach_review | Review my DNA results with a coach (30 min) | $39.95 | book | assess | One 1:1 video session to go through results |
| hh_coach_membership | Nutrition Coaching Membership | $99/month | subscribe + book first session | assess | Bi-weekly 1:1 sessions with a registered dietitian, 5-day meal plans, tracking, 20% off products |
| hh_kit_coaching | DNA Kit + Coaching Membership | $149 kit + $99/month | subscribe | assess | Kit and coaching together |
| hh_upload_coaching | Existing DNA data + Coaching Membership | $99 + $99/month | subscribe | assess | Upload and coaching together |
| hh_coach_premium | Health Coaching Membership (Premium) | $99/month | subscribe + book | assess | Lifestyle and nutrition strategy, including on/off GLP-1 transitions |
| hh_maintenance | Maintenance Coaching Membership | $299 | subscribe | ask | Periodic dietitian check-ins after a core program |
| hh_dna_blood | DNA + Blood Nutrition Program | $99/month | subscribe | assess | DNA analysis + 2 blood nutrient panels + coaching + 3 months of vitamins |
| hh_glp1_program | Steady on GLP-1 Program (4 months) | $129 | subscribe | assess | Support for people on or starting GLP-1 medication: protein, digestion, muscle, habits; with coaching |
| hh_programs | Goal programs inside coaching: weight loss, menopause, healthy aging | included | info | assess | The coach picks the program |

### 3.3 Vitamins and nutrition products (recommend = ask only; no dosing advice)

| Code | Product | Price | Kind |
|---|---|---|---|
| hh_vit_1m | HelixVit personalized vitamin, 1 month | $84 | subscribe |
| hh_vit_3m | HelixVit personalized vitamin, 3-month supply | $237 (boosters $258–$300) | buy |
| hh_vit_6m | HelixVit personalized vitamin, 6-month supply | $408 (boosters $438–$516) | buy |
| hh_vit_kit | HelixVit + DNA kit bundle | $349 | buy |
| hh_blend | HelixBlend personalized fiber & protein powder | $76 | buy |
| hh_glp1_protein | GLP-1 plant protein powder | $39.95 | buy |
| hh_glp1_multi | GLP-1 daily multivitamin | $24.95 | buy |
| hh_glp1_probiotic | GLP-1 probiotic | $19.95 | buy |
| hh_whey | Whey protein isolate (vanilla / chocolate) | $49.95 | buy |
| hh_multi_gummies | Multivitamin gummies | $16.95 | buy |
| hh_greens / hh_reds | Greens / reds powder | $35.95 / $29.95 | buy |
| hh_vitd | Vitamin D3 | $19.95 | buy |
| hh_magnesium | Magnesium glycinate | $26.95 | buy |
| hh_gut_probiotic | Gut balance probiotic + prebiotic | $29.95 | buy |
| hh_other_supp | Berberine, CoQ10, resveratrol, bone & heart support | $19.95–$29.95 | buy |
| hh_meals | Personalized prepared meal | $12.50 each | buy |
| hh_giftcard | Gift card | $25–$175 | buy |

Branded clothing and accessories in the real catalog are left out (not relevant to the bot).

### 3.4 Partner services (not sold by Helix & Harvest)

From the assessment tables: fitness coaching, sports nutrition, gut-health evaluation (primary care / GI), lab tests
(HbA1c, lipids, celiac), genetic counseling. In the demo these are **book** items with mock providers. Med spa
consultations route to **Essence** (the other demo client), shown as a referral, not a booking inside the Helix &
Harvest chat.

## 4. Fake links

Every buy or subscribe item gets a deterministic fake link:

```
https://shop.helixharvest.example/checkout?item=<code>&ref=<short user ref>
```

The `.example` domain never resolves, so nobody can pay by accident. In the web chat the link opens a small built-in
"Demo checkout — no payment taken" page instead.

## 5. Mapping to the assessment service codes

| Tables doc code | Helix & Harvest product |
|---|---|
| s_dna_test | hh_dna_kit (or hh_dna_upload if the user already has raw DNA data) |
| s_dna_coaching | hh_coach_membership / hh_kit_coaching |
| s_dna_upload | hh_dna_upload |
| s_programs | hh_programs |
| s_glp1_program | hh_glp1_program |
| s_dietitian | hh_coach_review or hh_coach_membership (coaches are registered dietitians) |
| s_labs (nutrients) | hh_dna_blood; other labs → partner |
| s_fitness, s_sports_nutrition, s_gut_eval, s_genetic_counsel | Partner (mock providers) |
| s_medspa | Referral to Essence |
| s_medical_weight | Referral to Essence medical weight management |
