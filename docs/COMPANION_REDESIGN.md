# Aster as an independent Health & Longevity Companion — analysis and design (draft v0.1)

Status: **implemented on branch `companion` (phases C0–C6, 2026-11-01/02).** Real-LLM check at the end of C6:
23 scripted scenarios (49 turns) all pass, average reply 3.1 s; four simulated users (`scripts.simulate_patients
c1 c2 c3 p1`) reviewed by hand. Demo steps: [DEMO_SCRIPT.md](DEMO_SCRIPT.md) Part A.

Supersedes the "multi-brand" phase P3 of [WEIGHT_GUT_ENGINEERING.md](WEIGHT_GUT_ENGINEERING.md) §3. Instead of
"one bot per brand", there is now **one Aster** and providers (Essence, Helix & Harvest, …) are catalog entries.
The assessment engine and rules from P2 are reused unchanged.

---

## 1. Current system (what exists today)

```
WhatsApp / web chat → webhook (signature, dedupe, store) → 200
  → pipeline._process: buttons → commands → safety prefilter (L4 = fixed reply, no LLM)
      → onboarding (welcome + consent buttons) → human mode → main agent
  → agent: ONE system prompt (Essence recovery companion) + 7 tools → JSON output
  → post-check (dose / dismissive / unbacked booking) → risk merge → handoff → send
scheduler (30 s) → due recovery check-ins / appointment reminders (template outside 24 h)
```

| Module | What it does | Essence-specific? |
|---|---|---|
| `channels.py` | WhatsApp Cloud API: text, reply buttons (≤3), templates, typing; parses `button_reply` **and `list_reply`**; console + web channels | No |
| `pipeline.py` | ingest, per-user lock, deferred messages until consent, button confirmations (`pa:`, `slot:`), human mode | Welcome text only |
| `agent.py` | single agent loop, tools: `search_knowledge`, `propose_journey`, `record_recovery_update`, `check_availability`, `book_appointment`, `list_my_appointments`, `request_handoff`; booking API adapter | **Prompt is 100% Essence**; includes a "be proactive: offer the earliest slot" sales rule |
| `context.py` | per-turn context: journey, protocol, symptom timeline, check-in, appointments, handoff | Recovery only |
| `journeys.py` + `protocols/*.yaml` | treatment journeys, recovery journal, symptom trends | Essence treatments |
| `followups.py` | protocol → scheduled check-ins, appointment reminders, send window, STOP | Essence |
| `booking.py` | mock slots + real Aster booking API (`appointments_for(phone)`, `availability`, `book`) | Essence |
| `handoff.py` | DB-built staff summary, staff WhatsApp alert, human mode | Essence staff |
| `safety.py` | emergency phrase rules, negation, fixed replies, output post-check | Generic |
| `knowledge.py` | BM25 over reviewed Essence website content | Essence |
| `assessment_engine.py` + `assessment_rules/weight_gut.yaml` | pure weight / nutrition / gut rules: BMI, energy need, exercise METs, diet class, gut red flags, tests, ≤2 recommendations | No (not wired into chat yet) |
| `brands/helix_harvest_catalog.yaml` | fictional nutrition provider catalog | Helix |
| `commands.py` | STOP/START/HUMAN/DELETE MY DATA, `#demo …` scenarios | Partly |
| `admin.py`, `static/chat.html` | demo console, WhatsApp-style web chat | — |

Data: `users` (one row per `wa_id`, consent, `conversation_mode` ai/human/paused), `messages`, `journeys`,
`journal_entries`, `symptom_observations`, `checkins`, `appointments`, `handoffs`, `pending_actions`, `agent_runs`.
There is **no** long-term profile, no goals, no plan, no provider concept and no mode concept.

Open branches not yet in `main`: `booking-buttons` and `llm-fallback` (both touch `agent.py`). The earlier decision
was to merge them before touching `agent.py`; that still applies (§10, C0).

## 2. What can be reused

| Reuse as is | Reuse with changes | Replace / remove |
|---|---|---|
| Channel adapters, webhook, dedupe, per-user lock, 24 h window logic | `pipeline._process` gets an entry/mode router step | The single Essence-only `SYSTEM_PROMPT` → shared core + module sections |
| Safety prefilter, fixed emergency reply, post-check, risk merge, handoff + human mode | Consent text: Aster is independent, providers only get data with permission | The "be proactive: offer earliest slot when they ask about a service" rule |
| Journeys, journal, symptom trends, protocols, check-in scheduler (= Module 1) | `Journey`, `Appointment`, `Handoff` get a `provider_slug` | Welcome copy ("stay connected with your care team") |
| Booking adapters (mock + API), `appointments_for(phone)` for record lookup | `handoff.notify_staff` routes by provider (Essence team vs Aster team) | — |
| `assessment_engine.evaluate()` (BMI, energy, exercise, diet, gut, special populations, red flags) | Its `services` output feeds **only** Module 3, never the plan | — |
| Helix catalog YAML, Essence knowledge files | Become provider catalog entries | — |
| FakeLLM test harness, scenarios, simulated patients | New flow tests per module | — |

## 3. Main changes: Essence bot → independent companion

1. **Identity.** Aster is the product; Essence is one linked provider. Nothing in the core prompt names Essence.
2. **Entry.** The user's source (Essence, other provider, direct, returning) is recorded and decides the first
   steps; "Essence client" is a provider link, not a bot type.
3. **Modes.** Conversation state has an explicit `active_mode` (companion / post_treatment) the user can switch at any
   time without losing progress.
4. **Long-term memory.** New structured profile (facts with source + timestamp), goals, assessment progress and
   versioned plans — separate from chat history and from provider records.
5. **Plans, not funnels.** The assessment ends in a personal plan with no products. Readiness depends on information
   sufficiency for the user's goal, not on a question count.
6. **Referral is gated in code.** Provider matching runs only on an explicit need or a "yes, show me options",
   uses a neutral catalog-driven matcher, discloses partnerships, and respects declines.
7. **Booking becomes request-driven.** In Module 1 the bot offers a visit only when the user asks, when the protocol
   has a clinical follow-up visit, or when risk ≥ 2 — never as an upsell.

## 4. Module architecture

One agent, one pipeline, shared memory, three specialised workflows.

```
inbound → pipeline
  A. buttons (consent:, entry:, menu:, mode:, pa:, slot:, asmt:, ref:)   ← deterministic, no LLM
  B. commands (STOP, START, HUMAN, MENU, /companion, /aftercare, /plan, RESTART …)
  C. safety prefilter (global, every mode)
  D. onboarding: consent → entry triage (only if source unknown)
  E. human mode (unchanged)
  F. router: returning-user resume? → active_mode
  → agent(core prompt + module rules, tools for active mode + switch_mode)
       Module 1  post_treatment : existing journey / recovery / booking tools (provider-scoped)
       Module 2  companion      : record_profile_facts, get_assessment_state, skip_question,
                                  set_goal, generate_plan, update_plan
       Module 3  referral       : find_services (code-gated), share_summary_with_provider (consent button)
       shared                   : search_knowledge, request_handoff, switch_mode
  → post-check (+ unrequested provider mention, product in plan, missing disclosure) → send
```

- **System prompt** = shared core (identity, safety, style, output) + three static module sections. It is static so
  it stays prompt-cached; the *active mode and its state* go in the per-turn context block.
- **Tools per mode.** The tool list is computed from `st.mode` on every step, so if the agent calls
  `switch_mode` mid-turn, the new module's tools are available in the same turn (no second LLM call).
- **Module 3 is not a mode.** It's a capability both modes can trigger, gated by code (§6.4).

### Why not three bots
Shared safety, memory and consent must not diverge; users move between topics in one chat; and the current code
already has one agent loop with tool filtering (`allow_tools`), so mode routing is a small change.

## 5. WhatsApp mode switching — platform capabilities and the chosen approach

| Capability (Cloud API) | Limits | Fit |
|---|---|---|
| Persistent menu (like Messenger) | **Not available** on WhatsApp | — |
| **Commands** (Conversational Automation API) | shown when the user types `/`; each has a name + hint; arrive as normal text messages | ✅ closest thing to an always-available menu |
| **Ice breakers** | up to 4 prompts, ≤80 chars, no emoji; shown only on an empty chat (first open) | ✅ first-contact entry choice |
| **Welcome message** event (`request_welcome`) | fires on first contact | ✅ start onboarding before the user types |
| **Reply buttons** | ≤3 buttons, title ≤20 chars, only inside the 24 h window | ✅ contextual switches, triage |
| **List message** | ≤10 rows, row title ≤24 chars, description ≤72; only inside the 24 h window | ✅ the main menu |
| CTA URL button | 1 URL button | provider booking link |
| WhatsApp Flows | multi-screen forms | ❌ overkill; breaks the conversational assessment |
| Templates | required outside 24 h; marketing templates need opt-in | proactive messages only |

**Chosen:** a *summoned* menu, not a fake persistent one.
1. Commands configured once via a setup script: `/menu`, `/companion`, `/aftercare`, `/plan`, `/human`.
2. Typed keywords work too: `MENU`, `HOME`, `SWITCH`.
3. `MENU` sends a **list message** (rows depend on the user):
   `My health companion` · `Post-treatment & visits` (only with a provider link) · `My plan` ·
   `Find a service` · `Talk to a person` · `Privacy & settings`.
4. Ice breakers on first open: "Help me with my health goals", "Questions after my treatment", "What is Aster?".
5. Contextual buttons at natural transitions only (end of a check-in, plan delivered), e.g.
   `[Health goals] [Menu]` — never appended to every message.
6. The agent can also switch on intent with `switch_mode` ("actually, a question about my filler…").
7. Web chat: list rendering + a ☰ quick-menu chip that sends `MENU`.

Switching only changes `users.active_mode`; assessment progress, plan and journeys live in their own tables, so
nothing is lost.

**Button label limits.** The requested labels are too long for WhatsApp (20 chars). Proposed:
body text "Have you had care or treatment at Essence before?" + buttons
`Yes, Essence client` · `No, my health goals` · `Skip for now`.

## 6. State design

### 6.1 Onboarding and entry

```
new ──welcome+consent──▶ consent_pending ──I Agree──▶ active ──▶ entry resolved? ──yes──▶ route by source
                               │ Not now                          │ no
                               ▼                                  ▼
                           declined                    entry triage (3 buttons) ──▶ route
```

| Source | How it is known | First step after consent |
|---|---|---|
| Essence | provider QR / link with a prefilled code (`wa.me/<n>?text=Hi Aster (ESS)`), CTWA `referral` field, web chat `?src=essence` | provider link `essence/referred` → "Is this about a recent treatment, or something else?" `[My treatment] [Health goals]` |
| Other provider (Helix…) | same, with its code | provider link for *that* provider; companion with that provider's focus (e.g. nutrition); **never** asked the Essence question |
| Direct | no code | entry triage buttons |
| Returning | user has consent + history | resume (§7) — no triage |

"Yes, Essence client" creates a `self_reported` link. Optional, consent-gated record lookup: "Want me to look up
your Essence visits so you don't have to type them?" `[Yes, look them up] [I'll tell you]` → `appointments_for(phone)`
→ proposed journey with the existing confirm buttons. Being an Essence client never forces post-treatment mode.

### 6.2 Modes

`users.active_mode`: `none` (just onboarded / menu) · `companion` · `post_treatment`.

| From | Trigger | To |
|---|---|---|
| any | `MENU` / `/menu` | list menu, mode unchanged until a row is tapped |
| any | menu row / `/companion` / `/aftercare` / contextual button | that mode |
| any | agent `switch_mode` on clear intent | that mode (same turn) |
| post_treatment | check-in wrapped up | stays; offers `[Health goals]` **once** per journey (Module 1 → 2 invitation) |
| any | urgent handoff | `conversation_mode=human` (orthogonal to `active_mode`, unchanged) |

### 6.3 Companion assessment and plan readiness

**Slots, not questions.** `app/assessment_rules/companion.yaml` defines slots per domain
(sleep, nutrition, movement, stress, recovery, body/weight, habits, medical context, preventive care). Each slot has:
`purpose` (why we ask), `ask_hint`, 1–2 `example_answers`, `value_schema` (enum / number+unit / short text),
`ttl_days` (how long an answer stays fresh), `sensitive` flag, and which `weight_gut` engine field it maps to.

**Goals pick slots.** `goals:` maps each goal (sleep better, more energy, lose weight, build fitness, gut health,
stress, healthy aging, skin/appearance, general check-up) to `core` and `supporting` slots and the domains to
explore. Users can have several goals; one is the `focus`.

**Assessment record** (`companion_assessments`): `status`, `focus_goal`, `current_slot`, `asked_slots`,
`skipped_slots`, `intro_shown`. Answers are **not** stored here but in `profile_facts`, so they're shared with Module 1,
reused by the next assessment, and never re-asked while fresh.

```
not_started ─intro (why + what you'll get, "skip anytime")─▶ in_progress ◀──resume── paused
     in_progress ─user leaves / "later" / switches mode─▶ paused
     in_progress ─readiness ≥ preliminary + (agent offers | user asks)─▶ plan_ready ─generate─▶ plan_active
     plan_active ─new facts / goal change / "update my plan"─▶ updating ─▶ plan_active (version+1)
     any ─"start over"─▶ new assessment (old facts kept unless the user deletes them)
```

**Readiness** (pure function, code-owned):

| Level | Rule (for the focus goal) | Behaviour |
|---|---|---|
| `blocked` | GI red flag, or under 13 | no plan; the safety reply + handoff |
| `not_ready` | no goal, or < 60 % of core slots *known* | keep exploring; if the user asks for a plan anyway → preliminary plan with explicit limitations |
| `preliminary` | ≥ 60 % core known | agent may offer a first plan; plan marked "first version" + lists what's missing |
| `ready` | every core slot known **or explicitly skipped**, and ≥ 50 % supporting known | agent offers the full plan |

Skipped slots count as *resolved* (we stop asking) but not *known* (they lower confidence and appear as limitations).
Stale facts (older than `ttl_days`) count as unknown and are confirmed, not re-asked from scratch
("Last month you said ~6 h sleep — still about right?").

**Why this design:** (1) readiness is tied to whether we can say something sensible about *this* goal, not to a
count; (2) skips never block a plan, they reduce its confidence; (3) code picks the next slot (highest-priority,
unresolved, not fresh in the profile), so we never re-ask and every question has a purpose; (4) the LLM only phrases
the question, gives the short feedback on the last answer, and extracts values into a validated schema;
(5) the existing `weight_gut` engine plugs in for weight/nutrition/gut slots (BMI, energy, exercise volume,
red flags, minors/pregnancy, disordered eating).

**Turn shape in companion mode:** one short reaction to the answer (specific, no generic praise) → optional one-line
"why it matters" → the next question with its purpose and, for hard questions, the YAML example answers.

**Plan** (`plans`, versioned; `status` active/superseded): `goal`, `situation_summary`, `priorities` (≤3),
`daily_habits`, `weekly_habits`, `metrics` (what to track, how often), `review_after` date, `confidence`
(preliminary/standard), `limitations`, `based_on_fact_ids`. Code builds the skeleton (goal, known/missing slots,
engine findings, candidate metrics from YAML); the LLM drafts items via `generate_plan`; code validates (sections
present, ≤3 priorities, **no provider or product names**) and stores it. Plan text is sent as 1–2 messages.

### 6.4 Referral (Module 3)

```
idle ─user asks for a service / professional─────────────▶ matching
idle ─agent sees a plausible professional need─▶ offered ("Want me to suggest options?")  [once per need]
offered ─yes─▶ matching        offered ─no─▶ declined (cooldown 30 days for that need)
matching ─▶ options_shown (neutral advice first, ≤3 options, reasons, uncertainty, disclosure)
options_shown ─interest─▶ details (price / booking / contact)  ─▶ contacted | booked
options_shown ─not now─▶ declined
```

- Gate in code: `find_services` returns `blocked` unless the turn has `user_requested_service` or an accepted offer,
  and the need isn't in cooldown. Offers are logged in `referral_offers`.
- Clinical escalation (handoff, "see your doctor / urgent care") is **not** a referral and is never gated.
- **Matcher** `providers.match(need, prefs, links)` — pure function over `app/providers/*.yaml`:
  1. candidates whose services serve the need code (reuses `weight_gut` service codes + a few new ones);
  2. hard filters: location / virtual, age and special-population limits, consult-first rules, budget if given;
  3. score: need fit, constraints met, evidence note;
  4. **tie-break only:** a provider the user already has a link with (Essence for Essence clients) goes first when
     scores are equal; for others, no default preference;
  5. always includes the neutral option where relevant ("your primary care doctor" / "a registered dietitian
     near you").
- Each provider file has `relationship` (`partner`, `paid_referral: true|false`) and a fixed disclosure sentence that
  must appear when that provider is shown (post-check enforces it).
- Sharing data with a provider needs a consent button per referral: `[Share my summary] [Don't share]`.

### 6.5 Module 1 changes (Essence post-treatment)
Kept: journeys, confirmation buttons, recovery journal, protocols, check-ins, red-flag probes, handoffs, booking tools.
Changed: tools and data scoped by `provider_slug`; booking offers only on request / protocol follow-up / risk ≥ 2;
"just aftercare questions" never ends with a booking push; one invitation to the companion after a wrap-up.

## 7. Long-term memory, exit and resume

| Layer | Where | Lifetime | Who sees it |
|---|---|---|---|
| Chat state | `users.active_mode`, `companion_assessments.current_slot`, `pending_actions` | session | the agent |
| Long-term profile | `profile_facts` (domain, key, value, source, recorded_at, superseded_at) + `user_goals` | until changed / deleted; TTL marks staleness | the agent; providers only via consented summary |
| Provider records | `journeys`, `journal_entries`, `appointments` with `provider_slug` | per provider | that provider's staff + the agent |
| Plans | `plans` (versioned) | kept | the user, the agent |
| Raw history | `messages` | last 20 in prompt | — |

- **Resume:** if the last inbound was > 12 h ago, the router adds a `RETURNING` block (last mode, paused slot, plan
  version + age) and the agent opens with one line + buttons `[Continue] [Update my plan] [Something else]`.
- **Exit mid-assessment:** nothing to save explicitly — every answer is already a fact; status becomes `paused`
  lazily (no reply for 2 h, or a mode switch). Abandoned after 60 days.
- **Restart / change goal / skip:** `RESTART` and `set_goal` create a new assessment that reuses fresh facts;
  `skip_question` marks the slot skipped.
- **Proactive messages:** recovery check-ins as today (STOP pauses). Companion follow-ups are **not** in the MVP;
  later they need an explicit opt-in button and an approved utility template. `marketing_opt_in` is a separate flag,
  default false, never set by silence.
- **Isolation:** every query is keyed by `user_id`; provider records filtered by `provider_slug`; Essence staff
  alerts never include companion facts; companion safety handoffs go to the Aster care team; family history and
  other `sensitive` slots never leave the profile; `DELETE MY DATA` / `#reset` wipe the new tables too.

## 8. Files and data structures to change

| File | Change |
|---|---|
| `app/models.py` | `User` + `entry_source`, `active_mode`, `marketing_opt_in`, `last_session_at`; `Journey`, `Appointment`, `Handoff` + `provider_slug` (default `essence`); new `ProviderLink`, `ProfileFact`, `UserGoal`, `CompanionAssessment`, `Plan`, `ReferralOffer` (all additive; `_add_missing_columns` handles old DBs) |
| `app/router.py` (new) | entry code parsing, triage, menu list, mode switch, resume block |
| `app/companion.py` (new) | load `companion.yaml`, next slot, readiness, fact validation, plan skeleton/validation; calls `assessment_engine` |
| `app/assessment_rules/companion.yaml` (new) | domains, slots, goals, metrics |
| `app/providers.py` + `app/providers/{essence,helix_harvest}.yaml` (new) | catalog loader + pure matcher; Helix file reuses the existing catalog |
| `app/agent.py` | prompt split (core + M1/M2/M3), per-mode tools, new tools, remove proactive-booking rule |
| `app/context.py` | mode-aware context: PROFILE, GOAL, ASSESSMENT, PLAN, REFERRAL, compact journey line |
| `app/pipeline.py` | welcome/consent copy, entry triage, router step, `menu:`/`entry:`/`mode:`/`ref:` buttons |
| `app/channels.py`, `app/outbound.py` | `send_list`; parse `request_welcome` and `referral` |
| `app/commands.py` | MENU, slash commands, PLAN, RESTART; wipe new tables |
| `app/safety.py` | post-check: provider/product names in plans, unrequested provider mention, missing disclosure |
| `app/handoff.py` | route alerts by provider; exclude sensitive facts |
| `app/static/chat.html`, `app/webchat.py`, `app/admin.py` | list messages, ☰ menu chip, `?src=` entry, console shows mode / profile / plan / referrals |
| `scripts/setup_whatsapp_commands.py` (new) | configure commands + ice breakers + welcome |
| `tests/` | new unit + flow tests (§10) |

## 9. MVP vs later

**MVP (this round)**: entry sources + triage; consent copy; menu (list + commands + keywords); mode switching with
kept progress; companion assessment for 5 goals (sleep, energy, weight, fitness, stress) with slots, feedback,
examples, skip/pause/resume; preliminary + full plan, versioned; plan update; referral gate, matcher, disclosure,
decline cooldown; Module 1 kept with the booking-push removed; Essence record lookup via booking API (mock in demo);
returning-user resume; isolation rules; demo console views; tests.

**Later**: companion follow-up reminders (opt-in + templates); weekly metric tracking/logging and trend charts;
more goals (healthy aging, skin, gut beyond the weight_gut flow); wearable/lab imports; provider dashboards;
real Essence CRM integration; multi-language; WhatsApp Flows for structured logging; real referral contracts and
attribution; clinician review of the companion rules.

## 10. Phases, tests and acceptance

| Phase | Scope | Done when |
|---|---|---|
| C0 | Merge `booking-buttons` + `llm-fallback` into `main`; new branch `companion` from `weight-gut-assessment` | all tests green on the new branch |
| C1 | Models, `companion.yaml`, `companion.py` (next slot, readiness, plan validation), providers YAML + matcher — pure code, no agent changes | unit tests pass |
| C2 | Entry + router: consent copy, triage, menu list, commands, modes, resume; `send_list`; web chat list + `?src=` | flow tests for the 4 entry types and switching |
| C3 | Agent split: core + modules, per-mode tools, `switch_mode`, Module 1 provider-scoped, booking push removed | all existing Module 1 tests pass (adjusted only where the push was intended) |
| C4 | Companion assessment + plan in chat | assessment / plan flow tests; simulate a sleep-goal chat end to end |
| C5 | Referral: gate, `find_services`, disclosure, consent-to-share, cooldown, post-check | referral tests |
| C6 | Demo console, demo script, real-LLM scenarios + personas, WhatsApp commands script, README | report reviewed |

**Test scenarios (each becomes a FakeLLM flow test and/or a real-LLM scenario)**
1. Direct user → consent → "No, my health goals" → companion intro within one message; no provider named anywhere
   in a 15-turn sleep chat.
2. Essence link (`ESS` code) → no triage question → "My treatment" → existing filler check-in flow works.
3. Helix link → never asked about Essence, `entry_source=helix_harvest`, never treated as an Essence client.
4. Mid-assessment `/aftercare` question → answered in Module 1 → `/companion` → the same next slot, nothing re-asked.
5. Leave after 3 answers, return next day → "Welcome back" + `[Continue]` → continues at slot 4.
6. "Just give me a plan" after 1 answer → preliminary plan, limitations listed, stored as v1.
7. All core slots answered → agent offers plan → plan has all sections, no products; "update my plan" → v2.
8. "Can someone help me with my diet?" → neutral advice first + options with reasons and disclosure.
9. Essence client, tied options → Essence first; non-client same need → order by fit only (unit test on matcher).
10. Decline an offer → no offer for that need for 30 days (code-blocked even if the LLM tries).
11. Aftercare-only questions → no booking offer; GLP-1 side effects → risk 2 handoff + visit offer (clinical, allowed).
12. Red flag in companion mode → same emergency path as today; companion facts not in Essence staff alert.
13. STOP → no check-ins; silence never sets `marketing_opt_in`.
14. `DELETE MY DATA` / `#reset` → profile, goals, plans, assessments, referral offers removed.

**Acceptance**: all old tests pass (except intentionally changed booking-push assertions); every scenario above
passes; in 10 real-LLM companion chats: ≤1 question per message, a specific reaction before each next question,
0 unrequested provider mentions, 0 products in plans, disclosure present whenever a partner is shown; reply time
within 10 % of today.

## 11. Decisions (2026-11-01)

These override anything above that says otherwise.

| # | Topic | Decision |
|---|---|---|
| D1 | Open branches | `booking-buttons`, `llm-fallback` and `weight-gut-assessment` each get a PR into `main`; the user merges them; `companion` branches from `main` |
| D2 | Paid referrals | Treat every provider as `partner`; disclosure: "Aster partners with X. This is an option, not a medical recommendation." |
| D3 | Essence record lookup by phone | Counts as authorized and reliable only after the user taps `Yes, look them up`; demo uses mock records |
| D4 | Companion safety handoffs | Go to an "Aster care team" (demo: the same staff numbers, tagged `Aster`) |
| D5 | Button labels | As proposed in §5 |
| D6 | Companion reminders | Not in the MVP |
| D7 | Referral decline cooldown | 30 days per need |
| D8 | Clinical review | `companion.yaml` is a draft, demo only, like `weight_gut` |
| D9 | MVP structured goals | **Only weight / nutrition / gut**, built on the `weight_gut` engine. Other goals (sleep, stress, fitness, aging, …): Aster talks about them with general, science-informed guidance and saves the goal to the profile, but there is no structured assessment or plan for them yet; it says a fuller plan for that goal is coming later |
| D10 | Medical background | **Not asked.** No conditions, medications or pregnancy questions. Age, sex, height and weight are still asked (needed for the engine). If the user volunteers pregnancy, a condition or medication, the existing safety rules still apply (special-population handoff, no calorie target, "check with your doctor") |
| D11 | Purchases | **No checkout links.** Every provider gives information plus contact details or its booking link; Essence keeps in-chat booking. The Helix catalog's `kind: buy/subscribe` items are shown as "available from Helix & Harvest" with contact details |

Effect on §9: the MVP goals list is replaced by D9; §6.3 slots exist only for the weight/nutrition/gut goals plus
the shared `goal` slot. Because of D10, family history is not a slot (stored only if volunteered), and diet-plan
examples don't mention medication. Under 18, pregnancy or a nutrition concern don't block the plan: the engine already
drops BMI categories, calorie targets and weight-loss advice, and the plan carries the YAML caution note.
