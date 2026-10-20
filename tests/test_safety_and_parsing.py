import hashlib
import hmac

import pytest

from app import booking, journeys, knowledge, safety
from app.channels import parse_webhook, verify_signature


@pytest.mark.parametrize(
    "text",
    [
        "Actually my vision has been a bit blurry since this morning",
        "There's a white patch on my upper lip and it's turning kind of purple",
        "my lips are turning dusky",
        "I can't breathe properly",
        "I fainted after getting home",
        "I don't know why my vision is blurry",
        "honestly I want to die",
        # Paraphrases found by the simulated-patient run (2026-10-20)
        "My throat feels really tight since the injection",
        "Everything looks foggy out of my left eye",
        "I can barely get any air in",
        "My upper lip has gone a weird greyish colour",
        "theres a small spot on my upper lip that looks kind of greyish? idk if its just the bruise",
        "I feel like I'm going to pass out",
        "My face feels numb on one side and my smile looks off",
        "It's getting hard to swallow",
        # Negation must not swallow a real emergency
        "No fever, but I can't breathe",
        "No fever, I can't breathe",
        "no fever and chest pain is getting worse",
        "No, my vision is blurry",
        "No, chest pain",
        "No swelling and my vision is blurry",
    ],
)
def test_prefilter_emergency(text):
    assert safety.prefilter(text).level == 4


@pytest.mark.parametrize(
    "text",
    [
        "No pain or vision problems. Just swelling.",
        "no blurry vision at all",
        "I haven't had any blurry vision",
        "my lips are still a little swollen and I have some bruising",
        "I threw up twice yesterday",
        "what's the price of lip filler?",
        # Negated lists – the answer to Aster's own red-flag question
        "No fever, chest pain or trouble breathing. No, I don't need a consultation.",
        "Nope, no chest pain or trouble breathing",
        "I haven't had any fever, chest pain or trouble breathing",
        "No drooping, no double vision, and swallowing, breathing and talking all feel normal",
        "none of that, no fever, no chest pain, no trouble breathing",
        # Word traps
        "My vision of the final result was a bit different, but I love it",
        "This price is killing me haha",
        "I'm dying to try the HydraFacial",
        "the bruise is a bit purplish but fading",
        "my skin feels tight after the peel",
    ],
)
def test_prefilter_not_emergency(text):
    assert safety.prefilter(text).level == 0


def test_postcheck():
    assert "dosing_advice" in safety.postcheck("You could lower your dose to 0.25 mg", booked_this_turn=False)
    assert "dismissive_reassurance" in safety.postcheck("That's completely normal!", booked_this_turn=False)
    assert "unbacked_booking_claim" in safety.postcheck("You're all set, your consultation is booked!", booked_this_turn=False)
    assert safety.postcheck("You're all set, your consultation is booked!", booked_this_turn=True) == []
    assert safety.postcheck("Swelling can be common in the first few days.", booked_this_turn=False) == []
    # A refusal is not a booking claim (simulated patient run 2, patient 12)
    assert safety.postcheck("I can't say it's confirmed until I book the consult.", booked_this_turn=False) == []
    assert safety.postcheck("3 PM isn't booked yet, but I can check a consultation time.", booked_this_turn=False) == []


def test_signature():
    body = b'{"a":1}'
    sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature(body, sig, "secret")
    assert not verify_signature(body, "sha256=bad", "secret")
    assert not verify_signature(body, None, "secret")


def test_parse_webhook():
    payload = {"entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": "15551234567", "profile": {"name": "Emily Smith"}}],
        "messages": [
            {"from": "15551234567", "id": "wamid.1", "type": "text", "text": {"body": "hi"}},
            {"from": "15551234567", "id": "wamid.2", "type": "interactive",
             "interactive": {"type": "button_reply", "button_reply": {"id": "consent:yes", "title": "Yes"}}},
            {"from": "15551234567", "id": "wamid.3", "type": "image", "image": {"id": "x"}},
        ],
        "statuses": [{"id": "wamid.out", "status": "delivered"}],
    }}]}]}
    msgs, statuses = parse_webhook(payload)
    assert [m.type for m in msgs] == ["text", "button_reply", "media"]
    assert msgs[0].profile_name == "Emily Smith"
    assert msgs[1].button_id == "consent:yes"
    assert statuses[0]["status"] == "delivered"


def test_treatment_matching():
    assert journeys.match_treatment("I got lip filler") == "dermal_filler"
    assert journeys.match_treatment("my first Ozempic shot") == "glp1"
    assert journeys.match_treatment("NAD+ injection") == "nad_plus"
    assert journeys.match_treatment("CoolSculpting on my belly") == "body_contouring"
    assert journeys.match_treatment("a facial") == "generic"


def test_knowledge_search():
    hits = knowledge.search("how much is botox")
    assert hits and "Botox" in hits[0]["text"]
    assert any("CoolSculpting" in h["text"] for h in knowledge.search("do you offer coolsculpting"))
    assert knowledge.search("where are you located")[0]["url"].endswith("/our-clinic/")


def test_mock_availability_is_deterministic():
    a = booking.MockBookingAdapter("America/Indiana/Indianapolis")
    slots = a.availability("next Thursday")
    assert slots and all(s.weekday() == 3 for s in slots)
    assert slots == a.availability("next Thursday")
