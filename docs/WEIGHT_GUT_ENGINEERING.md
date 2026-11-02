# Weight, Nutrition & Gut Health Assessment + second brand — engineering design (draft v0.2)

Companion docs:
- [WEIGHT_GUT_ASSESSMENT.md](WEIGHT_GUT_ASSESSMENT.md) — the evidence tables (rules)
- [DEMO_CLIENT_HELIX_HARVEST.md](DEMO_CLIENT_HELIX_HARVEST.md) — the fictional nutrition brand and its catalog

Status: **design for review, no code yet.**

## 1. Goals and non-goals

**Goals**
1. **Two brands on one system.** Essence (med spa, recovery companion — unchanged) and **Helix & Harvest** (fictional
   DNA-nutrition brand, demo). Each brand has its own persona, facts, knowledge, catalog, staff and entry point.
2. In the Helix & Harvest chat, run the A1/A2 assessment one question at a time. Every question can be skipped and
   resumed later.
3. Turn answers into findings and **at most 2** recommendations using the rules, then let the user **book** (coaching,
   partner services) or **buy / subscribe** (reports, programs, products) through a **fake checkout link**.
4. Keep replies fast: the rules run in code, and the extra prompt text is loaded only while an assessment is active.
5. Store assessments over time, so a later assessment can say what changed.

**Non-goals (this round)**
- Real payments or real orders: a fake link only (client decision).
- Daily or monthly reminder plans (client: not now).
- Recommending supplements from findings, or any dose advice (client decision: buy on request only).
- A second agent, LangGraph or microservices (project rule).
- Sharing data between brands.

## 2. Client decisions this design follows

| Topic | Decision |
|---|---|
| New client | Fictional "Helix & Harvest". Fake name, email, phone and links; product structure modelled on a real public catalog |
| Brands | Two brands coexist; handoffs go to each brand's own team |
| Buying | "Click to buy" gives a fake link; nothing is really bought |
| Supplements / products | Can be described and bought when the user asks; never proactively recommended; no doses |
| Limited-evidence tests | Can be recommended when the goal matches, always with a fixed "evidence is limited" sentence |
| Sex question | Asked, skippable |
| Start | Both automatic (topic detected) and from a menu / keyword |
| Under 18, pregnant, breastfeeding | Allowed; no BMI category or calorie target; general guidance; **handoff to the team** |
| Restrictive / disordered eating | Keep the conversation going; no weight-loss advice; **handoff to the team** |
| Family history | Stored only, never shared |
| Food estimates | Bands only, never exact calories |
| Question flow | One question per message, buttons, progress, resume later |
| Scope | Widen the persona scope for nutrition, exercise and gut health |
| Branches | Merge `booking-buttons` and `llm-fallback` first, then rebase this branch before touching `agent.py` |

## 3. Architecture

### 3.1 Brand configuration

```
app/brands/
  essence.yaml          # today's hard-coded facts move here (persona, CLINIC FACTS, phone, links, features)
  helix_harvest.yaml    # new brand
app/brand.py            # load, get_brand(slug), brand_for_phone_number_id(id)
```

A brand file holds:
- `slug`, display name, persona line, the BRAND FACTS block, phone, email, links, privacy URL and consent text
- `features`: `recovery_journeys` (Essence: on, Helix: off), `assessment` (Essence: off, Helix: on), `commerce`
  (Essence: off, Helix: on)
- `knowledge_dir`, `catalog_file`, the booking providers / services (mock)
- the env var names for its WhatsApp `phone_number_id` and its staff numbers
- `referrals`: for example Helix → Essence for med spa and medical weight management (contact info only)

### 3.2 Routing

| Channel | How the brand is chosen |
|---|---|
| WhatsApp | The webhook payload has `metadata.phone_number_id` → brand. Outbound messages use that brand's number. One Meta app and one access token can serve several numbers |
| Web chat | `/chat?brand=helix_harvest&key=…` (default `essence`, so current links still work) |
| Scripts | `scripts.simulate --brand helix_harvest` |

### 3.3 What changes in existing code

| Area | Change | Risk control |
|---|---|---|
| `models.User` | + `brand` column; unique `(brand, wa_id)` instead of unique `wa_id` | One-off migration script for the Render Postgres (§9 Q3) |
| `agent.SYSTEM_PROMPT` | Split into a **shared core** (safety, risk, style, output) + a **brand section** (who you are, what you do, facts, booking specifics) | Essence's rendered prompt must be word-for-word the same as today → snapshot test |
| `knowledge.py` | One BM25 index per brand; `knowledge_content/<brand>/` | Essence files move folders only |
| `safety.emergency_reply`, `commands`, `pipeline` texts | Phone and brand name from the brand config | Existing tests unchanged |
| `handoff.notify_staff` | Sends to the brand's staff numbers | — |
| `booking.MockBookingAdapter` | Providers and services per brand | Essence slots unchanged |
| `admin.py`, `chat.html` | Brand filter / brand theme (name, colors, menu items) | — |
| `context.build_context_block` | Recovery parts only if `recovery_journeys`; assessment block only if active | — |

### 3.4 Pipeline (Helix & Harvest)

```
WhatsApp / web chat → brand resolved → pipeline._process
   A. buttons          ← "asmt:" answers recorded by code; "buy:" → checkout link
   B. commands         ← + ASSESSMENT keyword
   C. safety.prefilter ← + GI red-flag phrases
   D. consent          ← brand consent text (mentions nutrition, exercise, digestive and DNA information)
   E. human mode       (unchanged)
   → agent: shared core + Helix section
        tools: search_knowledge, search_catalog, start_assessment, record_assessment_answer,
               get_assessment_result, get_checkout_link, check_availability, book_appointment,
               list_my_appointments, request_handoff
   → post-check (+ no diagnosis, no dose, ≤ 2 recommendations, no unrequested supplement)
```

## 4. Assessment

### 4.1 Files

| File | Role |
|---|---|
| `app/assessment_rules/weight_gut.yaml` | The tables from WEIGHT_GUT_ASSESSMENT.md as data, keyed by row code, with `rules_version` |
| `app/assessment_engine.py` | Pure functions, no DB and no LLM (§4.4) |
| `app/assessment.py` | DB side: start / record / evaluate / resume; builds the `<assessment>` context block |

### 4.2 Data model

```python
class Assessment(TimestampMixin, Base):
    __tablename__ = "assessments"
    id, user_id (fk, cascade)
    kind: str                 # "weight_gut"
    status: str               # in_progress | completed | declined | abandoned
    rules_version: str        # "weight_gut@0.1"
    current_step: str | None  # q0 | a1_q1 | a1_q2 | a1_q3 | a2_q1 | a2_q2 | a2_q3 | summary
    answers: dict             # per step; each field = value | "unknown" | "skipped"
    derived: dict | None      # bmi, bmi_code, need_kcal range, activity level, diet class, flags, special_population
    recommendations: dict | None  # findings, tests, services (generic codes + brand product codes)
    completed_at, source_message_ids
```

- At most one `in_progress` row per user. Completed rows are kept, so the summary can mention changes.
- `in_progress` rows untouched for 30 days become `abandoned` (checked lazily).

### 4.3 Conversation

| Concern | Owner |
|---|---|
| Question order, next step, what's missing | Code |
| Wording, empathy, understanding free text | LLM |
| Free text → fields (units, foods, activities) | LLM through tool arguments; code validates and converts |
| BMI, calories, exercise volume, diet class, gut route, tests, recommendations | Code (engine + YAML) |
| Summary text | LLM, from the engine result only |

| Step | Asked as | Fast path |
|---|---|---|
| q0 goal | List message (6 rows) | `asmt:goal:<code>` |
| a1_q1 weight, height, age, sex | Free text + sex buttons (female · male · skip) | `asmt:sex:<code>` |
| a1_q2 exercise | Free text | "None" button |
| a1_q3 meals | Meals/day buttons, then foods as free text | `asmt:meals:<n>` |
| a2_q1 diet plan / GLP-1 | Free text | "No plan" button |
| a2_q2 GI / family history | Free text, with "this stays private" | "No issues" button |
| a2_q3 testing goal | List message | `asmt:testgoal:<code>` |
| summary | 1–2 messages + up to 2 action buttons (Book / Buy) | `slot:` / `buy:` buttons |

- Buttons are recorded by code without an LLM call; then the agent runs once to ask the next question.
- After A1 the bot gives an interim result and asks whether to continue with the 3 gut questions.
- Start: automatic when the user brings up weight, food, exercise or digestion (the agent asks "Want a quick
  2-minute check?" first), or the keyword `ASSESSMENT`, the web-chat menu, or a welcome-message button.
- "skip" records `skipped`; "stop" / "later" pauses; a later message on the topic offers to resume.
- Priority order: red flags > open handoff / human mode > assessment.

### 4.4 Engine

```python
bmi(weight_kg, height_cm) -> (value, code)
energy_need(weight_kg, height_cm, age, sex, activity_level) -> (low, high)    # Mifflin; DGA fallback
exercise_profile(activities) -> {category, weekly_moderate_eq_min, activity_level, kcal_week}
intake_band(items, need) -> code
diet_class(flags, intake_code, bmi_code) -> code
gut_route(gi_codes, family_codes) -> {red_flag, rows}
special_population(age, pregnant, breastfeeding, diet_flags, bmi_code) -> {minor, pregnancy, disordered_eating}
pick_tests(testing_goal, findings, max_n=2) -> [codes]
recommend(derived, brand_catalog) -> {findings, tests, services[:2], referrals, handoff_reason | None}
```

Hard rules in code (not only in the prompt):
- At most 2 recommendations (tests + services together).
- Products marked `recommend: ask` are never returned by `recommend()`.
- Disordered eating / BMI < 18.5 → no weight-loss services or deficit advice; `handoff_reason = "nutrition_concern"`.
- Under 18 / pregnant / breastfeeding → no BMI category or calorie target; `handoff_reason = "special_population"`.
- Red flag → no tests or products; the pipeline escalates.
- Med spa only when `goal = body_shape` and BMI < 25, as a **referral** to Essence.
- Grade C/X items always carry the YAML's fixed limitation sentence.

### 4.5 Agent tools

```text
start_assessment(goal?) -> {assessment_id, next_step, question_hint}
record_assessment_answer(step, fields, skipped?) -> {saved, derived_so_far, next_step, missing_fields, question_hint}
get_assessment_result() -> {sections_answered, findings[], tests[], services[], referrals[], notes_for_reply[]}
search_catalog(query) -> [{code, name, price_text, kind, recommend}]
get_checkout_link(product_code, variant?) -> {url, button_title}
```

- `fields` is validated against a JSON schema per step, with enums from the YAML. Bad values become `unknown`, and
  the tool returns a hint so the LLM asks once more.
- For a1_q3 the LLM passes `foods: [{item, portion: small|regular|large, kcal_est}]` and `diet_flags[]`. The food
  quick-estimate table is in the context only during that step.

### 4.6 Prompt and context
- Helix brand section: persona, what you do (assessment, products, booking), BRAND FACTS, the required FDA
  disclaimer rule (when describing products), supplement policy, the referral to Essence.
- The `<assessment>` block appears only while one is active: current step, answered and missing fields, question
  hint, flow rules. About 250–400 tokens, plus the food table during a1_q3.
- `PROMPT_VERSION` is per brand; `rules_version` is logged in `AgentRun`.

## 5. Commerce (fake checkout) and booking

| Item kind | Bot behaviour |
|---|---|
| `book` (coach review, coaching first session, partner services) | Existing `check_availability` → `book_appointment` with mock providers for the brand |
| `buy` / `subscribe` | `get_checkout_link` → a message with a **"🛒 Buy now"** URL button (WhatsApp `cta_url` interactive message) |
| `info` | Describe only |

- The link is `https://shop.helixharvest.example/checkout?item=<code>&ref=<ref>`. The `.example` domain never
  resolves. In the web chat, the button opens `/demo-checkout?item=…`, a small page saying "Demo checkout — no
  payment taken".
- No order table. Each link the bot sends is logged as a tool call in `AgentRun`. The Demo Console lists "checkout
  links sent".
- The bot never says an order was placed or shipped. "Where's my order?" → "This is a demo; no real orders are
  placed" (demo mode), or a routine handoff.
- Cross-brand referrals (Helix → Essence) give Essence's contact details and booking link only. No data is passed
  across, and the bot can't book the other brand's slots.

## 6. Safety and handoffs

| Trigger | Action | Mode after |
|---|---|---|
| GI red flags (blood in stool, black stool, vomiting blood, can't keep fluids down, severe belly pain, unintentional weight loss) | Prefilter level 3 → fixed urgent reply + urgent handoff | human |
| Existing L4 emergency phrases | Unchanged | human |
| Disordered-eating signals (restrictive, very low intake, loss of control, BMI < 18.5 with a weight-loss goal) | Routine handoff `nutrition_concern`; kind reply; no weight-loss advice | AI (routine handoffs don't switch modes) |
| Severe signs with it (fainting, not eating for days, chest pain) | Urgent handoff | human |
| Under 18 / pregnant / breastfeeding | Routine handoff `special_population`; general guidance only | AI |
| Under 13 | Stop collecting health data; routine handoff (COPPA) | AI, general information only |
| GLP-1 side effects | Risk 2 + handoff to the prescribing clinician (as today) | AI |
| Post-check | Block "you have <condition>", "you are deficient", dose amounts, "this will cure / treat", more than 2 recommendations, supplement names the user didn't ask about | — |
| Privacy | Family history excluded from handoff summaries, staff alerts and booking notes; `DELETE MY DATA` / `#reset` delete assessments | — |

## 7. Performance
- The engine takes microseconds; there is no extra LLM call per turn (buttons save one).
- Extra prompt text only during an active assessment; the food table only during a1_q3.
- Target: no reply-time regression > 10% in `scripts/run_scenarios` (both brands).

## 8. Testing

| Layer | What |
|---|---|
| Brand regression | Essence's rendered prompt equals the current prompt (snapshot); all existing tests pass unchanged |
| Engine unit tests | BMI edges, unit conversion, Mifflin (female / male / unknown), METs → minutes → level, diet class, gut red flags, `pick_tests`, the cap of 2, `recommend: ask` never returned, med spa trigger, special populations, disordered eating |
| Flow tests (FakeLLM) | Brief cases A–G; skip / resume; buttons; checkout-link button; booking a coach; handoffs for minors and disordered eating; deletion |
| Routing | Webhook `phone_number_id` → brand; web chat `?brand=`; same phone in both brands = two users |
| Real-LLM scenarios | 8 new chats with checks: ≤ 1 question per message, ≤ 2 recommendations, no exact calories, unknowns not guessed, the disclaimer sentence present, no unrequested supplements |
| Personas | Gym-goer (BMI 27, muscular), GLP-1 user with nausea, bloating + family history of celiac, 16-year-old, restrictive eater |

## 9. Phases (stop for review after each)

| Phase | Scope | Done when |
|---|---|---|
| P1 | These three docs | Approved |
| P2 | `weight_gut.yaml`, Helix catalog YAML, `assessment_engine.py`, unit tests (no `agent.py` changes) | Unit tests pass; YAML matches the tables doc |
| P3 | Multi-brand refactor: brand configs, routing, prompt split, per-brand knowledge / booking / staff, web chat `?brand=`, migration script | Essence snapshot + all old tests pass; Helix chat answers brand questions |
| P4 | Assessment in the chat: model, tools, context block, entry points, buttons, flow tests | Cases A–G pass in tests and in `scripts.simulate --brand helix_harvest` |
| P5 | Commerce links, `search_catalog`, Helix booking providers, safety and handoff rules, privacy, Demo Console | Safety tests pass; book + buy-link flows work end to end |
| P6 | Real-LLM scenarios, personas, reply-time check, demo script section | Report reviewed |

`booking-buttons` and `llm-fallback` must be merged into `main` (and this branch rebased) before P3.

## 10. Open questions — with proposed defaults

The build follows the default unless you say otherwise.

| # | Question / concern | Proposed default |
|---|---|---|
| Q1 | Is there a second real WhatsApp number for Helix & Harvest? | Demo Helix in the **web chat** first (`?brand=helix_harvest`). Add the number later; routing already supports it |
| Q2 | Same person messaging both brands | Two separate users; nothing is shared |
| Q3 | The Render Postgres has a unique index on `users.wa_id` | A one-off migration script in `scripts/` (drop the index, add the column and the composite index). Demo data can be reset if needed |
| Q4 | Clinical review of the rules | `rules_version` stays "draft"; demo only until a clinician signs off |
| Q5 | Helix consent and privacy text | New consent mentioning nutrition, exercise, digestive and DNA information; fake privacy URL on the `.example` domain |
| Q6 | FDA disclaimer | Added once per conversation when the bot first describes a product's benefits |
| Q7 | Names of Helix coaches and partner providers | Fictional names (e.g. "Maya Chen, RD"), clearly demo |
| Q8 | Disordered eating: what counts as "severe" for an urgent handoff | Fainting, not eating for days, chest pain or heart racing, or the user says they feel unsafe |
| Q9 | Under 13 (COPPA) | Don't collect health data; general information + handoff |
| Q10 | Order status questions | Demo mode: "no real orders are placed"; otherwise a routine handoff |
| Q11 | The client brief (.docx) | Move it to `references/` and commit it with these docs |
| Q12 | Price accuracy | Prices are copied from the public catalog on 2026-10-29 and marked "demo prices" |
