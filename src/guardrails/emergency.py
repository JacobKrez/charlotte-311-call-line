"""Emergency triage.

A 311 non-emergency line is, by definition, the wrong place to report an
emergency. But people call it anyway -- often because they do not know
the distinction, sometimes because they cannot reach 911, and sometimes
because they are testing whether the system will actually help.

The failure this module exists to prevent: a caller says "someone is
breaking into my house right now", the intent matcher files it as a
police report with a location and a time, the system says "your report
has been filed, ID 8f3a...", and the resident hangs up believing help is
coming. Nothing is coming. The report will sit in a queue for hours.

So emergency detection runs *before* intent classification, and when it
fires the call does not continue through the report-filing state
machine at all. It escalates to a human.

Design constraints:

* **Deterministic and offline.** No model call, no network. An
  escalation path that depends on an API is an escalation path that
  fails during the incident it exists for.
* **High recall, low precision, by design.** A false positive costs a
  human handoff on a non-emergency call -- expensive but survivable. A
  false negative can cost a resident their safety. The asymmetry sets
  the threshold, so the keyword sets here are broad on purpose.
* **No 911 dispatch.** This module never says "help is on the way." It
  says "I am connecting you to a human agent" and stops. The prototype
  does not dial 911 and does not claim to have dispatched anything.

Three classes of phrase are treated differently, and the difference
matters more than it might look:

1. **Unconditional.** Weapons, fire with trapped occupants, active
   self-harm, medical emergencies. These escalate no matter what else
   is in the transcript.
2. **Presence in the dwelling.** "Someone is in my house." This
   escalates *unless* the transcript explains who they are, because a
   plumber doing scheduled work is not an intruder. The exemption list
   is short and each entry is a specific, common case.
3. **Intrusion in progress.** "Breaking into", "broke in". Present
   progressive escalates. Past tense does not: "someone broke into my
   car last night" is a police report, and treating it as an emergency
   would make the line unusable for the single most common non-
   emergency call it receives.

The detector is a keyword and phrase matcher over a normalized
transcript. That is weak against paraphrase, and the red-team corpus
records that weakness rather than hiding it -- see
``redteam/attack_cases.jsonl`` and the "Known limitations" section of
the README.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Words that mark a sentence as describing something happening now, as
# opposed to something that happened earlier.
_IMMEDIACY_MARKERS = (
    "right now",
    "happening now",
    "in progress",
    "currently",
    "as we speak",
    "at this moment",
    "still happening",
    "ongoing",
    "just now",
    "happening",
)

# Class 1: unconditional escalation. These are severe enough that
# context does not soften them.
_UNCONDITIONAL_PHRASES = (
    # Weapons
    "has a gun",
    "have a gun",
    "holding a gun",
    "with a gun",
    "has a knife",
    "have a knife",
    "pulled a gun",
    "pulled a knife",
    "pointing a gun",
    "gun at me",
    "gun at my",
    "knife at me",
    "shot at me",
    "shot at my",
    # Fire and entrapment
    "the house is on fire",
    "my house is on fire",
    "building is on fire",
    "trapped in",
    "trapped inside",
    "stuck in the burning",
    "smoke filling",
    # Self-harm
    "going to kill myself",
    "going to hurt myself",
    "kill myself",
    "end my life",
    # Medical emergency
    "not breathing",
    "stopped breathing",
    "choking",
    "unconscious",
    "overdose",
    "heart attack",
    "collapsed and",
    # Threats against the caller
    "threatening me",
    "threatening to kill",
    "wants to kill me",
    "gun to my head",
)

# Class 2: presence in the dwelling. Escalates unless the transcript
# explains who is there.
_PRESENCE_PHRASES = (
    "breaking in",
    "breaking into",
    "is in my house",
    "is in our house",
    "in my home right now",
    "someone is inside",
    "they are inside",
    "is breaking in",
    "is breaking into",
    "someone is in the house",
    "intruder",
    "home invasion",
    "is inside the house",
    "inside the house",
    "inside my house",
)

# Contexts that explain a presence without making it a threat. Each one
# is a specific, common, non-emergency case. This list is short on
# purpose: a long list turns the emergency detector into a
# non-detector.
_BENIGN_PRESENCE_CONTEXT = (
    "plumber",
    "electrician",
    "maintenance",
    "landscaper",
    "delivery",
    "delivery driver",
    "scheduled",
    "appointment",
    "expected",
    "authorized",
    "landlord",
    "inspector",
    "repairman",
    "worker",
    "technician",
    "visiting",
    "let in",
    "let me in",
)

# Class 3: harm to a third party.
_OTHER_PERSON_EMERGENCY = (
    "my child is missing",
    "my kid is missing",
    "my daughter is missing",
    "my son is missing",
    "my baby is missing",
    "someone is hurting",
    "she is being abused",
    "he is being abused",
    "being abused",
    "he is hurting her",
    "she is hurting him",
)

# Past-tense intrusion verbs. These describe something that already
# happened, which is a police report rather than an emergency.
_PAST_INTRUSION_PHRASES = (
    "broke into",
    "broke in",
    "was broken into",
    "were broken into",
    "had broken into",
)


@dataclass(frozen=True)
class EmergencySignal:
    """A detected emergency.

    ``kind`` is a coarse label used for attribution in the red-team
    harness and the audit log. It is never shown to the caller.
    """

    kind: str
    matched: str


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace so matching is whitespace-agnostic."""
    return re.sub(r"\s+", " ", text.lower()).strip()


def _has_benign_presence_context(normalized: str) -> bool:
    """True if the transcript explains who is present in the dwelling."""
    return any(marker in normalized for marker in _BENIGN_PRESENCE_CONTEXT)


def _is_past_tense_intrusion(normalized: str) -> bool:
    """True if the only intrusion language in the transcript is past tense."""
    present_tense = (
        "breaking in",
        "breaking into",
        "is breaking in",
        "is breaking into",
    )
    if any(phrase in normalized for phrase in present_tense):
        return False
    return any(phrase in normalized for phrase in _PAST_INTRUSION_PHRASES)


def detect_emergency(transcript: str) -> EmergencySignal | None:
    """Detect an emergency in a caller turn.

    Returns an :class:`EmergencySignal` if the turn describes an
    immediate threat to life or safety, otherwise ``None``.

    The caller is expected to escalate on a non-``None`` return. A
    ``None`` return is *not* an assertion that the turn is safe -- it
    means no known emergency pattern matched.
    """
    if not transcript:
        return None
    normalized = _normalize(transcript)

    # Class 1: unconditional.
    for phrase in _UNCONDITIONAL_PHRASES:
        if phrase in normalized:
            return EmergencySignal(kind="immediate_threat", matched=phrase)

    # Harm to a third party.
    for phrase in _OTHER_PERSON_EMERGENCY:
        if phrase in normalized:
            return EmergencySignal(kind="person_in_danger", matched=phrase)

    # Class 2: presence in the dwelling, with the benign-context
    # exemption and the past-tense exemption.
    if _is_past_tense_intrusion(normalized):
        return None

    for phrase in _PRESENCE_PHRASES:
        if phrase in normalized:
            if _has_benign_presence_context(normalized):
                return None
            return EmergencySignal(kind="immediate_threat", matched=phrase)

    return None
