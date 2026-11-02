# Aster — Longitudinal State → Context → Personalized Actions

A multimodal agent system exploring long-term memory and cross-provider orchestration for health and wellness. It keeps track of user state over time, connects context across services, and proposes personalized next actions that reflect where the user actually is — not just what they said in the last message.

In this domain, memory is not a convenience feature. It is the difference between a generic reminder and a relevant next step. At the same time, memory goes stale, states change, and not every action should be automated. That tension is central to this work.

What this system explores:

- Longitudinal memory: how user state evolves over time, and how older context should be weighted, updated, or set aside when new information arrives.
- Cross-provider orchestration: how context from different services can be combined into one coherent picture without losing track of where each piece came from.
- Safe personalization: how to propose actions that are specific enough to be useful, while keeping permission, privacy, and human review built into exactly the places where automation should stop.

The system verifies its output, escalates when risk is high, and hands off to a human rather than acting alone. In high-stakes settings, a well-timed handoff is a feature, not a failure.

Scope: prototype / demo build on simulated data.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # add ANTHROPIC_API_KEY (default: Opus 5.5 fast), or OPENAI_API_KEY + LLM_PROVIDER=openai
.venv/bin/python -m scripts.simulate --name Emily    # chat in the terminal, no Meta account needed (--src essence)
.venv/bin/python -m pytest -q                        # tests use a scripted fake LLM
.venv/bin/python -m scripts.simulate_patients p1 p8   # LLM-played patients (costs tokens; pick personas)
.venv/bin/python -m scripts.run_scenarios            # scripted safety/booking checks + reply times (costs tokens)
.venv/bin/python -m scripts.benchmark_models gpt4.1 opus-low-fast   # compare models on speed and checks
.venv/bin/uvicorn app.main:app --reload              # webhook server; /demo?key=ADMIN_TOKEN
```

## Web chat (no Meta account needed)

`/chat?key=ADMIN_TOKEN` is a WhatsApp-style mobile web chat that runs through the same pipeline as the
WhatsApp bot (consent buttons, agent, safety rules, handoffs, check-ins). The ⋮ menu has the demo scenarios and a
"Staff alerts" view showing what the clinic receives, and "New user – …" entries for a direct user, an Essence
referral or a Helix & Harvest referral (also `?src=essence` on the link). The ☰ button next to the input sends MENU. Web-chat users have ids starting with `web`; everything
else is unchanged. Use it to test before the WhatsApp number is set up, or as the demo fallback.

## Deploy (Render)
1. Push this repo to GitHub.
2. Render → **New → Blueprint** → choose the repo (`render.yaml` creates the web service and Postgres).
3. Fill in the secret env vars: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`/`OPENAI_MODEL` (optional), `WA_ACCESS_TOKEN`,
   `WA_PHONE_NUMBER_ID`, `WA_APP_SECRET`, `STAFF_WHATSAPP_NUMBERS`.
4. Point the Meta webhook at `https://<service>.onrender.com/webhooks/whatsapp` (see docs/META_SETUP.md).

## How it works (demo build)

Aster is one agent with three modules (docs/COMPANION_REDESIGN.md): **companion** (the user's own health goals:
progressive assessment → versioned personal plan), **post_treatment** (Essence recovery check-ins, aftercare,
booking) and **referral** (need-driven provider matching, gated in code). The user switches with `MENU`, `/`
commands or buttons; progress lives in its own tables, so switching loses nothing.

```
WhatsApp → /webhooks/whatsapp (verify signature, dedupe, store) → 200
        → background: entry source (provider code / ad / ?src=) → buttons → menu & modes → commands
        → safety rules (emergency = fixed reply, no LLM) → onboarding (consent → "Essence client?" once)
        → agent: shared core prompt + module rules; tools of the active mode only (+ switch_mode)
             companion: set_goal, record_profile_facts, skip_question, pause_assessment, get_plan_inputs, generate_plan
             post_treatment: search_knowledge, propose_journey, record_recovery_update, check_availability, book_appointment …
             referral (both): offer_options, find_services, decline_options, share_with_provider
        → post-check (dosing, dismissive, unbacked booking, unrequested provider) → risk merge (incl. rule risk)
        → handoff (Essence team or Aster care team) → send (+ partner disclosure, plan text, buttons)
scheduler (every 30s) → due check-ins → LLM-written opener (or approved template outside 24h)
```

| Module | Role |
|---|---|
| `app/channels.py` | WhatsApp Cloud API + console channel: text, buttons, list messages; webhook parsing, signature |
| `app/pipeline.py` | inbound pipeline, onboarding/consent, button confirmations, risk merge, disclosures |
| `app/router.py` | entry sources, menu (list message), modes, resume, plan display, sharing with a provider |
| `app/agent.py`, `app/llm.py` | system prompt, tools per mode, structured output; Anthropic / OpenAI / Fake providers |
| `app/companion.py`, `app/assessment_rules/companion.yaml` | slots, next question, plan readiness, plan checks (pure) |
| `app/assessment_engine.py`, `app/assessment_rules/weight_gut.yaml` | weight / nutrition / gut rules (pure) |
| `app/memory.py` | long-term profile facts, goals, assessment progress, plan versions |
| `app/providers.py`, `app/provider_catalog/` | provider catalog (one YAML per provider) and the neutral matcher |
| `app/referral.py` | referral gate: when options may be offered or shown, cooldown after a no |
| `app/safety.py` | emergency rules, fixed templates, output post-check |
| `app/knowledge.py`, `app/knowledge_content/` | reviewed Essence website content + BM25 retrieval |
| `app/journeys.py`, `app/protocols/` | journeys, recovery journal, symptom trends; draft follow-up protocols |
| `app/followups.py` | check-in planning, scheduler, appointment reminders |
| `app/booking.py` | **mock** availability/booking or the Aster booking API |
| `app/handoff.py` | handoff summary (facts from DB) + staff WhatsApp alert, per team |
| `app/admin.py` | read-only Demo Console |
| `scripts/setup_whatsapp_commands.py` | WhatsApp `/` commands, ice breakers and welcome (dry run by default) |

Adding a provider: add `app/provider_catalog/<slug>.yaml` (services by need code, locations, modes, contact,
disclosure, entry codes). No prompt or code changes.

**Demo simplifications vs. the design docs:** single process with an in-process scheduler (no worker or job
table), `create_all` instead of Alembic, in-memory BM25 instead of pgvector, no separate LLM triage call
(rules + agent risk + post-check), mock booking. Protocols and safety wording are drafts pending clinical sign-off.
