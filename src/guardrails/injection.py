"""Transcript-borne prompt injection.

The transcript is untrusted input. It was produced by an audio signal
that a caller controls, and the caller may be trying to steer the agent
away from its task.

On a text channel this is a known problem. On a voice channel it is
worse in two specific ways:

* **ASR error is adversarial error.** A caller cannot type
  "ignore previous instructions" precisely; they can *say* it, and the
  transcription may render it imperfectly. Detection has to be robust
  to the ways a phrase gets mangled, not just the clean spelling.
* **Social plausibility is high.** A caller mid-report who says "just
  file it, skip the rest" is making a normal-sounding request. The
  injection surface here is not only theatrical "ignore your
  instructions" attacks; it is ordinary impatience being laundered
  into an instruction the system should not accept.

The second point drives the design. The dangerous instruction is not
"ignore previous instructions" -- it is any attempt to make the system
skip a confirmation, fabricate a detail, or change what gets written to
the city's record. Confirmations and required slots exist to keep a
caller's own words on the record; removing them means the city files a
report the caller never made.

So the detector targets *instruction-shaped attempts to subvert the
confirmation flow*, and the canonical spelling is just one of the forms.

This layer refuses the turn. It does not lock the call into refusal
mode: the next turn is evaluated normally, so a caller who slips once
is not stuck talking to a brick.
"""

from __future__ import annotations

import re

# Canonical instruction-override phrasings.
_OVERRIDE_PHRASES = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "ignore the previous instructions",
    "disregard previous instructions",
    "disregard all instructions",
    "forget your instructions",
    "forget all previous instructions",
    "forget everything",
    "new instructions",
    "updated instructions",
    "system prompt",
    "you are now",
    "act as",
    "pretend you are",
    "developer mode",
    "dan mode",
    "jailbreak",
    "override your",
    "bypass your",
    "bypass the",
    "your new role",
    "from now on you",
)

# Role-override and spoofing markers.
_ROLE_MARKERS = (
    "system:",
    "assistant:",
    "developer:",
    "user:",
    "<|im_start|>",
    "<|im_end|>",
    "[system]",
    "[inst]",
)

# Attempts to skip or falsify the confirmation flow. These are the
# highest-value targets on this line, because they are what turn a
# partial conversation into a false record.
_CONFIRMATION_BYPASS = (
    "skip the confirmation",
    "skip confirmation",
    "skip the questions",
    "skip all questions",
    "without asking",
    "without asking me",
    "don't ask me",
    "do not ask me",
    "no need to ask",
    "just file it",
    "just submit it",
    "just save it",
    "file it anyway",
    "file it now",
    "submit it anyway",
    "confirm it for me",
    "confirm for me",
    "say yes for me",
    "mark it confirmed",
    "mark as confirmed",
    "you already have",
    "you already know",
    "make up",
    "make up a",
    "invent a",
    "just put",
    "put anything",
    "put the address",
    "put some address",
    "anything for location",
    "whatever for location",
    "just put anything",
    "assume the location",
    "assume it happened",
    "say it happened",
    "confirm without",
    "just confirm it",
    "file with a fake",
)

# Attempts to alter the recorded content of a report.
_CONTENT_OVERRIDE = (
    "change the details",
    "change what i said",
    "change the location",
    "change the address",
    "edit the report",
    "edit what i said",
    "write it as",
    "record it as",
    "log it as",
    "say instead",
    "put that instead",
    "add that it",
    "remove the location",
    "leave the location blank",
    "leave the address blank",
)

_ALL_PHRASES = _OVERRIDE_PHRASES + _CONFIRMATION_BYPASS + _CONTENT_OVERRIDE


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace.

    Punctuation is stripped because ASR frequently omits it or renders
    it inconsistently: "ignore previous instructions." and "ignore
    previous instructions" and "ignore, previous instructions" are the
    same utterance. Removing punctuation also removes the cheapest way
    to slip a phrase past a naive substring check.
    """
    lowered = text.lower()
    # Keep alphanumerics and whitespace only.
    stripped = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return re.sub(r"\s+", " ", stripped).strip()


def detect_injection(transcript: str) -> bool:
    """Return True if the turn attempts to subvert the call's task.

    Matching is done on a punctuation-stripped, whitespace-collapsed
    transcript so that trivial formatting does not defeat it.
    """
    if not transcript:
        return False
    normalized = _normalize(transcript)

    for phrase in _ALL_PHRASES:
        if _normalize(phrase) in normalized:
            return True

    # Role markers need a slightly looser test, since ": " after a role
    # word is a strong signal on its own.
    for marker in _ROLE_MARKERS:
        if marker in transcript.lower():
            return True

    return False
