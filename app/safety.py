"""Safety layer – independent of the agent persona.

1. prefilter(): deterministic emergency-phrase rules (run before any LLM call).
2. Agent self-reported risk_level (structured output).
3. postcheck(): rules applied to the agent's reply before it is sent.
risk_final = max(rule, agent) – risk can only go up.

DEMO: phrases and templates are drafts pending clinical and legal review.
"""

import re
from dataclasses import dataclass, field

RISK_NAMES = {0: "routine", 1: "expected", 2: "review", 3: "urgent", 4: "emergency"}

# (code, pattern). Patterns run on lower-cased text.
_L4_RULES: list[tuple[str, str]] = [
    ("breathing", r"(can'?t|cannot|can not|hard to|trouble|difficulty|struggling to|barely) (breathe|breathing)|short(ness)? of breath|(can'?t|barely|cannot) get (any |enough )?air|gasping"),
    ("throat_swelling", r"(throat|tongue) (is |feels )?((really|very|so|super|kind of|kinda) )?(swelling|swollen|closing|tight)"),
    ("swallowing", r"(hard|harder|difficult|struggling) to swallow|(can'?t|cannot) swallow|(trouble|difficulty|problems?) swallowing"),
    ("chest_pain", r"chest (pain|tightness|hurts|pressure)"),
    ("fainting", r"\b(fainted|passed out|blacked out|losing consciousness|lost consciousness)\b|(going to|gonna|about to) (pass out|faint|black out)|feel(ing)? (really |very )?faint\b"),
    ("vision_change", r"\b(vision|eyesight|sight)\b\W+(?:\w+\W+){0,4}?(blurry|blurred|blurring|fuzzy|dim|dark|darker|doubled|double|gone|changed|changing|weird|worse|loss)\b|(blurry|blurred|double|lost|losing|loss of|changes? in( my)?) (vision|eyesight|sight)|can'?t see|(foggy|cloudy|hazy|blurry|blurred) (out of|in) (my )?((left|right) )?eye|everything (looks|is|went) (foggy|blurry|hazy|cloudy)"),
    ("skin_ischemia", r"(white|pale|blanch\w*|grey|gray|dusky|purple|blue|mottled|blotchy) (patch|spot|area|skin|lip|lips)|(lip|lips|skin) (is |are |turning |turned |looks? )+(white|pale|grey|gray|dusky|purple|blue|mottled|blotchy)|(greyish|grayish|bluish|purplish)( \w+){0,2} (spot|patch|area|colou?r|skin|tinge)|(lip|lips|skin|spot|patch|area) (\w+ ){0,4}(greyish|grayish|bluish|purplish)|(gone|going|turned|turning) (a )?((weird|strange|funny) )?(grey|gray|white|pale|dusky|blue) colou?r"),
    ("face_droop", r"(face|mouth|smile) (is )?droop|(numb|weak|droop\w*) on one side|one side of my face (is |feels |looks |went |has gone )?(numb|weak|droop\w*)"),
    ("seizure", r"\bseizure|convuls"),
    ("self_harm", r"(kill|hurt|harm) myself|suicid|want to die|end my life|don'?t want to (live|be alive)"),
    ("anaphylaxis", r"anaphyla|(face|lips) (are |is )?swelling up .*(hives|breath)"),
]

_NEGATIONS = {"no", "not", "never", "without", "dont", "don't", "didnt", "didn't", "havent", "haven't",
              "hasnt", "hasn't", "isnt", "isn't", "nor", "zero"}


@dataclass
class RuleResult:
    level: int = 0
    hits: list[str] = field(default_factory=list)
    negated_hits: list[str] = field(default_factory=list)


# Words that start a new statement, so a negator before them no longer applies ("no fever, I can't breathe").
_CLAUSE_WORDS = {"i", "i'm", "im", "i've", "ive", "my", "me", "it", "it's", "its", "he", "she", "they", "we", "you",
                 "is", "are", "was", "were", "feel", "feels", "felt", "getting", "got", "started", "now", "still",
                 "suddenly"}
_LIST_SEP = re.compile(r",|\b(or|nor|and)\b")


def _is_negated(text: str, start: int, end: int) -> bool:
    """Negated if a negator sits within the 3 words right before the match, in the same clause, or the match is an
    item in a negated list: "no fever, chest pain or trouble breathing".

    Deliberately narrow: "I don't know why my vision is blurry" and "no fever and chest pain is getting worse" must
    NOT count as negated. A missed negation costs a false alarm; a wrong negation could miss an emergency.
    """
    after = re.split(r"[.!?;,\n]|\b(?:or|nor|and|but)\b", text[end:])[0]
    if any(w in _CLAUSE_WORDS for w in after.split()):
        return False  # "no fever and chest pain is getting worse"
    sentence = re.split(r"[.!?;\n]|\b(?:but|except|however)\b", text[:start])[-1]
    clause = sentence.split(",")[-1]
    if any(w in _NEGATIONS for w in clause.split()[-3:]):
        return True
    tokens = sentence.split()
    # A bare "No," answers a question; it does not negate what follows ("No, chest pain").
    neg = max((i for i, w in enumerate(tokens) if w in _NEGATIONS), default=None)
    if neg is None:
        return False
    between = tokens[neg + 1:]
    return (
        0 < len(between) <= 10
        and bool(_LIST_SEP.search(" ".join(between)))
        and not any(w.strip(",") in _CLAUSE_WORDS for w in between)
    )


def prefilter(text: str) -> RuleResult:
    t = text.lower().replace("’", "'")
    result = RuleResult()
    for code, pattern in _L4_RULES:
        for m in re.finditer(pattern, t):
            if _is_negated(t, m.start(), m.end()) and code != "self_harm":
                result.negated_hits.append(code)
            else:
                result.hits.append(code)
                result.level = 4
                break
    return result


def emergency_reply(first_name: str | None, clinic_phone: str, hits: list[str]) -> str:
    name = f"{first_name}, w" if first_name else "W"
    if "self_harm" in hits:
        return (
            f"{name}hat you're feeling matters, and you don't have to go through it alone. "
            "Please call or text 988 (Suicide & Crisis Lifeline) now, or call 911 if you're in immediate danger.\n\n"
            "I've let the clinic team know so a person can reach out to you."
        )
    return (
        f"{name}hat you're describing needs urgent medical attention. "
        "Please call 911 or go to the nearest emergency room now — don't wait for an appointment.\n\n"
        f"I've alerted the Vanished team. If you can, also call the clinic at {clinic_phone}."
    )


HUMAN_MODE_ACK = (
    "Thanks — the Vanished team has your message and will be in touch. "
    "If anything feels urgent or gets worse, please call 911 or go to the nearest emergency room."
)

FALLBACK_REPLY = (
    "Sorry, I'm having trouble answering that right now. I've asked the Vanished team to follow up with you. "
    "If you feel unwell or something is getting worse, please call the clinic at (812) 320-2284, or 911 in an emergency."
)

# ---------- post-check on agent output ----------

_DOSE = re.compile(r"\b\d+(\.\d+)?\s?(mg|mcg|ml|units?|iu)\b.*\b(take|inject|increase|decrease|lower|raise|skip|dose)\b|\b(take|inject|increase|decrease|lower|raise|skip)\b.*\b\d+(\.\d+)?\s?(mg|mcg|ml|units?)\b", re.I)
_DISMISSIVE = re.compile(r"nothing to worry about|definitely normal|completely normal|perfectly normal|no need to (worry|see|contact)", re.I)
_STOP_MED = re.compile(r"you should (stop|continue|keep) (taking|your|the) ", re.I)
_BOOKED = re.compile(r"\b(booked|confirmed|scheduled|all set)\b", re.I)
_NEGATED_CLAIM = re.compile(r"\b(not|can'?t|cannot|won'?t|isn'?t|unable|couldn'?t|no|never)\b", re.I)


def postcheck(reply: str, *, booked_this_turn: bool, has_appointment: bool = False) -> list[str]:
    violations = []
    if _DOSE.search(reply):
        violations.append("dosing_advice")
    if _DISMISSIVE.search(reply):
        violations.append("dismissive_reassurance")
    if _STOP_MED.search(reply):
        violations.append("medication_advice")
    claims = [m for m in _BOOKED.finditer(reply)
              if not _NEGATED_CLAIM.search(" ".join(reply[:m.start()].split()[-4:]))]
    if (claims and re.search(r"appointment|consult|follow-?up|visit", reply, re.I)
            and not (booked_this_turn or has_appointment)):
        violations.append("unbacked_booking_claim")
    return violations
