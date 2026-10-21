"""Read-only demo page: shows the structured data Aster builds from WhatsApp conversations.
Not the staff dashboard (out of MVP scope) – it exists so the client can see what is stored."""

from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import journeys
from app.models import AgentRun, Appointment, Checkin, Handoff, JournalEntry, Message, SymptomObservation, User

RISK = {0: ("Routine", "r0"), 1: ("Expected", "r1"), 2: ("Review", "r2"), 3: ("Urgent", "r3"), 4: ("Emergency", "r4")}


def _t(dt, tz) -> str:
    return dt.astimezone(tz).strftime("%b %d %H:%M") if dt else "–"


async def render(db: AsyncSession, key: str) -> str:
    users = list(await db.scalars(select(User).order_by(User.last_inbound_at.desc().nulls_last())))
    cards = []
    for u in users:
        tz = ZoneInfo(u.timezone)
        js = await journeys.list_journeys(db, u.id)
        jhtml = []
        for j in js:
            obs = list(await db.scalars(select(SymptomObservation).where(SymptomObservation.journey_id == j.id)
                                        .order_by(SymptomObservation.created_at)))
            rows = "".join(
                f"<tr><td>Day {o.day_post_treatment}</td><td>{escape(o.symptom_code)}</td>"
                f"<td>{'absent' if not o.present else escape(o.severity or 'present')}</td>"
                f"<td class='trend {o.computed_trend or ''}'>{escape(o.computed_trend or '')}</td>"
                f"<td>{escape(o.frequency or o.free_text or '')}</td></tr>" for o in obs
            ) or "<tr><td colspan=5 class=muted>No symptoms recorded</td></tr>"
            entries = list(await db.scalars(select(JournalEntry).where(JournalEntry.journey_id == j.id)
                                            .order_by(JournalEntry.created_at)))
            ehtml = "".join(f"<li><b>Day {e.day_post_treatment}</b> {escape(e.summary)} "
                            f"<span class='pill {RISK[e.risk_level][1]}'>{RISK[e.risk_level][0]}</span></li>" for e in entries)
            day = journeys.day_post_treatment(u, j)
            jhtml.append(
                f"<div class=journey><h4>{escape(j.treatment_label_raw)} · {j.treatment_date:%b %d} "
                f"<span class=muted>(Day {day}, {escape(j.provider_name or 'provider n/a')}, {j.source})</span></h4>"
                f"<table><tr><th>Day</th><th>Symptom</th><th>State</th><th>Trend</th><th>Detail</th></tr>{rows}</table>"
                f"<ul class=entries>{ehtml}</ul></div>"
            )
        checkins = list(await db.scalars(select(Checkin).where(Checkin.user_id == u.id).order_by(Checkin.due_at)))
        chtml = "".join(f"<li>{escape(c.kind)} {'Day ' + str(c.day_offset) if c.day_offset is not None else ''} · "
                        f"{_t(c.due_at, tz)} · <b>{c.status}</b></li>" for c in checkins if c.status != "cancelled")
        appts = list(await db.scalars(select(Appointment).where(Appointment.user_id == u.id)))
        ahtml = "".join(f"<li>{escape(a.purpose)} · {_t(a.starts_at, tz)} · {escape(a.provider_name)} "
                        f"<span class=muted>(demo booking) {escape(a.notes or '')}</span></li>" for a in appts)
        handoffs = list(await db.scalars(select(Handoff).where(Handoff.user_id == u.id).order_by(Handoff.created_at.desc())))
        hhtml = "".join(f"<details><summary><span class='pill u-{h.urgency}'>{h.urgency}</span> {escape(h.reason)} · "
                        f"{h.status} · {_t(h.created_at, tz)}</summary><pre>{escape(h.summary)}</pre></details>"
                        for h in handoffs)
        runs = list(await db.scalars(select(AgentRun).where(AgentRun.user_id == u.id)
                                     .order_by(AgentRun.created_at.desc()).limit(8)))
        rhtml = "".join(
            f"<tr><td>{_t(r.created_at, tz)}</td><td>{r.kind}</td><td><span class='pill {RISK[r.risk_final][1]}'>"
            f"{RISK[r.risk_final][0]}</span></td><td>{escape(', '.join(t['tool'] for t in (r.tool_calls or [])))}</td>"
            f"<td>{r.latency_ms} ms</td><td>{escape(', '.join(r.postcheck_violations or []))}</td></tr>" for r in runs
        )
        msgs = list(await db.scalars(select(Message).where(Message.user_id == u.id)
                                     .order_by(Message.created_at.desc()).limit(300)))
        mhtml = "".join(f"<div class='msg {m.direction}'>{escape(m.body)}<span class=muted> · {_t(m.created_at, tz)}"
                        f"</span></div>" for m in msgs)  # newest first; column-reverse shows it at the bottom
        cards.append(
            f"<section class=card><h3>{escape(u.first_name or u.display_name or 'Unknown')} "
            f"<span class=muted>+{u.wa_id[:-4]}•••• · mode: {u.conversation_mode} · onboarding: {u.onboarding_state} · consent: {u.health_consent} "
            f"· check-ins: {'on' if u.proactive_opt_in else 'off'}</span></h3>"
            f"<div class=grid><div><h5>Treatment journeys</h5>{''.join(jhtml) or '<p class=muted>None</p>'}"
            f"<h5>Check-ins & reminders</h5><ul>{chtml or '<li class=muted>None</li>'}</ul>"
            f"<h5>Appointments</h5><ul>{ahtml or '<li class=muted>None</li>'}</ul>"
            f"<h5>Handoffs to staff</h5>{hhtml or '<p class=muted>None</p>'}"
            f"<h5>Agent runs</h5><table><tr><th>When</th><th>Kind</th><th>Risk</th><th>Tools</th><th>Latency</th>"
            f"<th>Post-check</th></tr>{rhtml}</table></div>"
            f"<div><h5>Conversation</h5><div class=chat>{mhtml}</div></div></div></section>"
        )
    return PAGE.replace("{{CARDS}}", "".join(cards) or "<p class=muted>No conversations yet.</p>")


PAGE = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Aster Demo Console</title><meta http-equiv=refresh content=5>
<style>
:root{--bg:#faf7f5;--card:#fff;--ink:#2b2420;--muted:#8a7f78;--line:#eee5df;--accent:#c06c84;--in:#fff;--out:#fde8ee}
@media (prefers-color-scheme:dark){:root{--bg:#1b1816;--card:#25211e;--ink:#f1e9e4;--muted:#a59a92;--line:#3a332e;--in:#2f2a26;--out:#4a2a35}}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
header{padding:16px;border-bottom:1px solid var(--line)}header b{color:var(--accent)}
main{padding:16px;max-width:1300px;margin:auto}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:3fr 2fr;gap:20px}@media(max-width:800px){.grid{grid-template-columns:1fr}}
h3{margin:0 0 8px}h4{margin:8px 0}h5{margin:16px 0 6px;text-transform:uppercase;font-size:11px;letter-spacing:.06em;color:var(--muted)}
.muted{color:var(--muted);font-weight:normal;font-size:12px}table{border-collapse:collapse;width:100%;font-size:13px}
td,th{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left}ul{margin:0;padding-left:18px}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:11px;background:var(--line)}
.r2,.u-routine{background:#fff1c2;color:#6b5200}.r3,.u-urgent{background:#ffd8b0;color:#7a3a00}.r4,.u-emergency{background:#ffc9c9;color:#8a0f0f}
.r1{background:#dff3e4;color:#1d5e33}.trend.worsening{color:#c0392b;font-weight:600}.trend.improving,.trend.resolved{color:#2e8b57}
.chat{display:flex;flex-direction:column-reverse;gap:6px;max-height:640px;overflow:auto}.msg{padding:8px 10px;border-radius:10px;white-space:pre-wrap;max-width:90%;border:1px solid var(--line)}
.msg.in{background:var(--in);align-self:flex-start}.msg.out{background:var(--out);align-self:flex-end}pre{white-space:pre-wrap;font-size:12px}
</style></head><body><header><b>Aster</b> demo console · Essence Aesthetics & Wellness · <span class=muted>simulated data only · refreshes every 5s</span></header>
<main>{{CARDS}}</main></body></html>"""
