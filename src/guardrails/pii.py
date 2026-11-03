"""PII redaction for transcripts and case records.

Two separate populations of personal data arrive on a 311 line:

* **Identifiers the caller volunteers** -- social security number,
  account number, phone number, email address. A 311 call about a
  pothole has no reason to contain an SSN, and when one appears it is
  either a mistake or something the caller is saying to be heard, not
  to be filed.
* **Identifiers the line needs** -- the location of the issue, the
  license plate of a towed vehicle, a callback number. These are the
  substance of the record and are **not** redacted. Redacting them
  would destroy the report.

So redaction is targeted, not blanket. It removes shapes that are
unambiguously personal identifiers and leaves city-relevant detail
alone. The tradeoff is deliberate: over-redaction that blanks a
location produces a useless case record, which is a worse failure than
a phone number surviving into a log that is itself access-controlled.

Free-form personal *names* are deliberately **not** covered. Detecting
"John Smith" in speech needs named-entity recognition, and a name
detector that fires on ordinary English ("will", "mark", "hernandez")
silently corrupts the record it is supposed to protect. A caller can
often be asked not to volunteer a name; an automatic false positive
cannot be un-fired. This limitation is recorded rather than papered over.
"""

from __future__ import annotations

import re

# SSN: nine digits with optional separators.
_SSN = re.compile(r"\b\d{3}[- ]?\d{2}[- ]?\d{4}\b")

# Account-number-shaped: 8+ consecutive digits, optionally grouped.
_ACCOUNT = re.compile(r"\b\d{8,}\b")

# US phone numbers in the common spoken and written forms.
_PHONE = re.compile(
    r"(?:\+?1[-. ]?)?"
    r"(?:\(\d{3}\)|\d{3})"
    r"[-. ]?"
    r"\d{3}"
    r"[-. ]?"
    r"\d{4}"
    r"\b"
)

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")

# Routing-number-shaped: 9 digits, typically spoken in groups.
_ROUTING = re.compile(r"\b\d{9}\b")

# Placeholders written in place of the real value.
_SSN_PLACEHOLDER = "[SSN REDACTED]"
_ACCOUNT_PLACEHOLDER = "[ACCOUNT REDACTED]"
_PHONE_PLACEHOLDER = "[PHONE REDACTED]"
_EMAIL_PLACEHOLDER = "[EMAIL REDACTED]"
_ROUTING_PLACEHOLDER = "[ROUTING REDACTED]"


def redact_pii(text: str) -> tuple[str, tuple[str, ...]]:
    """Redact personal identifiers from a transcript.

    Parameters
    ----------
    text:
        The raw transcript or detail string.

    Returns
    -------
    tuple[str, tuple[str, ...]]
        The redacted text and a tuple of notes naming each kind of
        value that was removed. The notes are written to the audit log
        so a reviewer can tell "nothing was redacted" apart from "the
        redactor ran and found nothing" -- the second is not
        distinguishable from the first without the note.
    """
    if not text:
        return text, ()

    notes: list[str] = []
    redacted = text

    # Order matters: SSN and account before the generic long-digit rule,
    # and routing before the generic rule, so the specific placeholder
    # wins.
    for pattern, placeholder, label in (
        (_SSN, _SSN_PLACEHOLDER, "ssn"),
        (_ROUTING, _ROUTING_PLACEHOLDER, "routing"),
        (_ACCOUNT, _ACCOUNT_PLACEHOLDER, "account"),
    ):
        if pattern.search(redacted):
            redacted = pattern.sub(placeholder, redacted)
            notes.append(label)

    if _PHONE.search(redacted):
        redacted = _PHONE.sub(_PHONE_PLACEHOLDER, redacted)
        notes.append("phone")

    if _EMAIL.search(redacted):
        redacted = _EMAIL.sub(_EMAIL_PLACEHOLDER, redacted)
        notes.append("email")

    return redacted, tuple(notes)
