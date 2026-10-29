# Aster — WhatsApp AI Companion (demo build)

WhatsApp recovery companion for Essence Aesthetics & Wellness. This is the **2-day demo build**: a working
slice of the design in `docs/`, using **simulated patient data only**.

- Design docs: [docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/AGENT_DESIGN.md](docs/AGENT_DESIGN.md)
- Demo: [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) · Meta setup: [docs/META_SETUP.md](docs/META_SETUP.md)

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # add ANTHROPIC_API_KEY (default: Opus 5.5 fast), or OPENAI_API_KEY + LLM_PROVIDER=openai
.venv/bin/python -m scripts.simulate --name Emily    # chat in the terminal, no Meta account needed
.venv/bin/python -m pytest -q                        # tests use a scripted fake LLM
.venv/bin/python -m scripts.simulate_patients p1 p8   # LLM-played patients (costs tokens; pick personas)
.venv/bin/python -m scripts.run_scenarios            # scripted safety/booking checks + reply times (costs tokens)
.venv/bin/python -m scripts.benchmark_models gpt4.1 opus-low-fast   # compare models on speed and checks
.venv/bin/uvicorn app.main:app --reload              # webhook server; /demo?key=ADMIN_TOKEN
```

## Web chat (no Meta account needed)

`/chat?key=ADMIN_TOKEN` is a WhatsApp-style mobile web chat that runs through the same pipeline as the
WhatsApp bot (consent buttons, agent, safety rules, handoffs, check-ins). The ⋮ menu has the demo scenarios and a
"Staff alerts" view showing what the clinic receives. Web-chat users have ids starting with `web`; everything
else is unchanged. Use it to test before the WhatsApp number is set up, or as the demo fallback.

## Deploy (Render)
1. Push this repo to GitHub.
2. Render → **New → Blueprint** → choose the repo (`render.yaml` creates the web service and Postgres).
3. Fill in the secret env vars: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`/`OPENAI_MODEL` (optional), `WA_ACCESS_TOKEN`,
   `WA_PHONE_NUMBER_ID`, `WA_APP_SECRET`, `STAFF_WHATSAPP_NUMBERS`.
4. Point the Meta webhook at `https://<service>.onrender.com/webhooks/whatsapp` (see docs/META_SETUP.md).

## How it works (demo build)

```
WhatsApp → /webhooks/whatsapp (verify signature, dedupe, store) → 200
        → background: buttons/commands → safety rules (emergency = fixed reply, no LLM)
        → agent (LLM + tools: search_knowledge, propose_journey, record_recovery_update,
                 check_availability, book_appointment, request_handoff) → structured output
        → post-check (no dosing / dismissive / unbacked booking claims) → risk merge → handoff → send
scheduler (every 30s) → due check-ins → LLM-written opener (or approved template outside 24h)
```

| Module | Role |
|---|---|
| `app/channels.py` | WhatsApp Cloud API + console channel, webhook parsing, signature |
| `app/pipeline.py` | inbound pipeline, onboarding/consent, button confirmations |
| `app/agent.py`, `app/llm.py` | system prompt, tools, structured output; Anthropic / OpenAI / Fake providers |
| `app/safety.py` | emergency rules, fixed templates, output post-check |
| `app/knowledge.py`, `app/knowledge_content/` | reviewed website content + BM25 retrieval |
| `app/journeys.py`, `app/protocols/` | journeys, recovery journal, symptom trends; draft follow-up protocols |
| `app/followups.py` | check-in planning, scheduler, appointment reminders |
| `app/booking.py` | **mock** availability/booking (to be replaced by Vagaro adapter) |
| `app/handoff.py` | handoff summary (facts from DB) + staff WhatsApp alert |
| `app/admin.py` | read-only Demo Console |

**Demo simplifications vs. the design docs:** single process with an in-process scheduler (no worker or job
table), `create_all` instead of Alembic, in-memory BM25 instead of pgvector, no separate LLM triage call
(rules + agent risk + post-check), mock booking. Protocols and safety wording are drafts pending clinical sign-off.
