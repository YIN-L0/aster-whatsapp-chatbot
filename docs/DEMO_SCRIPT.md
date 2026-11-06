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

Aster's replies are generated live, so the wording will differ slightly each time. Part A shows Aster as an
independent health & longevity companion; Part B shows the Essence post-treatment support it also offers
(patient lines from `example.docx`). In the web chat, ⋮ → "New user – …" starts each part with the right entry.

> Opening line: *Aster is the person's own long-term health companion. Essence and Helix & Harvest are partners it
> can connect them to when they want – not the point of the conversation.*

---

# Part A — Aster as a companion

## A1. A direct user and their own goal (4 min)
1. `#reset`, send **Hi!** → welcome + privacy + consent → **I Agree**
2. "Have you had care or treatment at Essence?" → **No, my health goals** → the opening ("I'm Aster, here to help you…") with the goal list
3. Tap **Set up a goal** → **Weight management** → Aster asks what kind (e.g. **lose weight**), explains why it will ask a few questions (any can be skipped) and asks the first one,
   with example answers
4. Answer naturally, e.g. **5'10 and about 210 lbs** → **38** → **skip that one** → a typical day of food
   → after each answer: one specific reaction, then the next question (never a form)
5. **Can you just give me a plan now?** → a first-version plan: priorities, daily and weekly habits, what to track,
   a review date, and what's still unknown. No products, no providers.
6. **My knees hurt with long walks, can you change that?** → version 2
7. Console: entry source, profile facts, assessment progress, both plan versions

> Talking point: plan readiness depends on whether Aster knows enough about *this* goal, not on a question count.
> Plans never contain products – that's checked in code.

## A2. Leaving and coming back (1 min)
- Mid-assessment, type **HUB** → **Your Longevity Hub**: **Continue My Assessment** is at the top, then **Set Up a
  Goal**, which opens the goal list again with each goal's status (✅ plan ready, ⏸ in progress). **My Care &
  Follow-ups** first asks a non-client which clinic treated them. Switch modes and back: nothing is lost.
- The next day (or after 12 h), **hi** → "Welcome back… want to pick up where we left off?"

## A3. When the user wants a professional (3 min)
1. **I've tried every diet and nothing sticks, I think I need someone to guide me** → neutral advice first; Aster may
   ask once "Would you like some options?" with buttons
2. **No thanks** → it carries on helping and won't offer again for 30 days (Console → Referrals)
3. In a new chat: **Can you recommend a dietitian? I live in Columbus, Ohio** → neutral advice, Helix & Harvest
   (online) and "a local RD, often covered by insurance"; the partner disclosure is added automatically
4. As an Essence client in Indianapolis: **Who could help me lose weight with medical support?** → Essence first
   (already a client, same fit) plus "your primary care doctor"; prices and how to start only after interest;
   "Share my summary" sends only what's shown on the button

> Talking point: providers appear only when the person asks or says yes. An existing relationship only breaks ties.

## A4. Safety in the companion (1 min)
**I want help with my digestion. I've had blood in my stool a few times this week.** → urgent handoff to the Aster
care team, no plan, no assessment questions – raised by the rules in code, whatever the AI thinks.

---

# Part B — Essence post-treatment support

## 0. Onboarding + clinic questions (2 min)
1. `#reset`, then send: **Hi!** → welcome + privacy link + consent buttons → tap **I Agree** → **Yes, Essence client**
   → **My treatment** (or open the web chat via ⋮ → "New user – sent by Essence")
2. **How much is Botox?** → starting prices for Botox/Dysport, taken from the website – and no booking push
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
- Demo Console: everything is structured data (journeys, symptom timeline, risk levels, handoffs; for the companion:
  profile, assessment, plans, referrals). Nothing lives only in the chat.
- After a calm check-in wrap-up, Aster offers the companion once ("…I can also help with your own health goals").
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
| Helix & Harvest | Fictional provider, fake contacts | Real partner agreements and referral terms |
| Companion rules (`companion.yaml`, `weight_gut.yaml`) | Drafts | Clinical review before real users |
| WhatsApp `/` commands and ice breakers | `scripts/setup_whatsapp_commands.py` (dry run by default) | Applied to the business number |
