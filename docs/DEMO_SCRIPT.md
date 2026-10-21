# Aster Demo Run-sheet

**Setup on screen:** your phone with WhatsApp (patient), a laptop with the Demo Console
(`https://<render-domain>/demo?key=<ADMIN_TOKEN>`, refreshes every 5 s), and optionally a second phone as the
"clinic staff" number.

**Before the meeting (15 min):**
- [ ] Patient phone and staff phone each sent `hi` to the test number today (opens the 24h window)
- [ ] `#reset` on the patient phone, then complete onboarding once
- [ ] Run every scenario once (the first LLM call after a deploy is slower)
- [ ] Demo Console open and logged in
- [ ] Backup: `python -m scripts.simulate` works on the laptop in case Wi-Fi or Meta has problems

Patient lines below are taken from `example.docx`. Aster's replies are generated live, so the wording will differ
slightly each time.

---

## 0. Onboarding + clinic questions (2 min)
1. `#reset`, then send: **Hi!** → welcome + privacy link + consent buttons → tap **I Agree**
2. **How much is Botox?** → starting prices for Botox/Dysport, taken from the website
3. **Do you do CoolSculpting?** → it doesn't make anything up: CoolSculpting isn't listed; it mentions truSculpt iD / Ignite RF
4. **Where are you located and how do I book?** → address and Vagaro link

> Talking point: answers come only from reviewed website content. If the content doesn't cover a question, Aster says so.

## 1. Natural journey creation (2 min)
1. **I got lip filler at Vanished yesterday with Carlie**
2. Aster asks you to confirm → **✅ That's right**
3. Aster: "Saved… I'll check in tomorrow"
4. Console: the journey, and Day 2 / 7 / 14 check-ins scheduled

> Talking point: AI-extracted data only becomes official after the patient confirms it. The check-in schedule comes from a per-treatment protocol, and the clinic's clinicians will own those protocols.

## 2. Scenario 3 — Lip filler, Day 2 (4 min)
`#demo filler 2 Emily` → Aster sends the Day 2 check-in and refers back to yesterday's swelling (seeded Day 1 entry).
1. **My lips are still a little swollen, and I have some bruising. Is that normal?**
   → explains this "can be common", and asks about severe pain, skin colour changes and vision changes
2. **No pain or vision problems. Just swelling. But I'm worried my lips look uneven.**
   → empathy, offers a follow-up with the injector (risk: *Review*, staff phone gets a 📋 alert)
3. **Yes, that would be great.** → offers a time next week
4. **Yes, perfect.** → confirmed (demo booking) + reminder + safety net
5. Console: swelling trend **improving** (moderate → mild), handoff summary, appointment, reminder

**Safety variant (the most important moment):** `#demo filler 2 Emily`, then
**Actually my vision has been a bit blurry since this morning**
→ fixed emergency reply (call 911), 🚨 alert on the staff phone, AI pauses. `#resolve` returns to AI mode.

> Talking point: emergency detection runs in code before the AI is involved. The AI can raise the risk level, never lower it.

## 3. Scenario 1 — GLP-1, Day 3 (3 min)
`#demo glp1 3 Sarah`
1. **I definitely don't have much appetite, which is good! But I've been feeling nauseous, and I threw up twice yesterday.**
   → asks about fluids, abdominal pain, dizziness
2. **I can drink water, and I don't have any pain. Just the nausea.**
   → recommends checking in with the provider instead of waiting; staff alert (Review)
3. **Yes, please.** → offers a slot · **Perfect!** → confirmed + "if you develop severe abdominal pain…"
4. Optional: **Should I lower my dose?** → no dosing advice; routed to the provider

## 4. Scenario 2 — NAD+, Day 1 (2 min)
`#demo nad 1 Michael`
1. **I'm feeling okay overall, but my arm is a little sore, and I felt slightly dizzy afterward.**
2. **The dizziness is gone. My arm is still a little sore, but there's no swelling.** → "I'll check in again tomorrow" (the next check-in really is scheduled)
3. **Sounds good. Actually, I'd also like to discuss whether I should continue these treatments.** → doesn't give an opinion; offers a consultation
4. **Yes, next Thursday would be great.** → Thursday slots · **Yes!** → confirmed
5. `#checkin` → time-travels to Day 2 and sends the next check-in, referring to yesterday's soreness

## 5. Scenario 4 — Body contouring, Day 1 (2 min)
`#demo body 1 Jessica`
1. **I'm feeling pretty good! Just a little sore and swollen. Can I go back to the gym?**
   → aftercare reminders ✅/⚠️ (from the client's sample, marked *draft – pending clinical sign-off*)
2. **Yes, please!** → follow-up booked · `#reminder` → shows the appointment reminder

## 6. Close (1 min)
- `#status` shows what Aster remembers for this patient.
- Demo Console: everything is structured data (journeys, symptom timeline, risk levels, handoffs). Nothing lives only in the chat.
- Next steps: the clinic signs off protocols and red flags; staff alert channel and SLA; Vagaro integration; compliance review before real patients.

---

## What is mocked in this demo (say it if asked)
| Area | Demo | Production plan |
|---|---|---|
| Appointment times and booking | Mock availability, stored as "demo booking" | Vagaro integration, or a handoff to the front desk |
| Follow-up protocols, red flags, aftercare text | Drafts written from the website and `example.docx` | Signed off by Vanished clinicians |
| Patient data | Simulated only | Real patients only after privacy/HIPAA review |
| WhatsApp number | Meta test number ("Test Number") | Clinic's verified business number + approved templates |
| Staff alerts | WhatsApp to one staff number | Clinic's chosen channel, on-call rota, SLA |
