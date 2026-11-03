"""Scope guard.

This line services non-emergency city requests and nothing else. A
caller asking a tax or legal question should be redirected, not
answered and not silently dropped.

The redirect preserves state. A caller who has already given a location
and a time and then asks an out-of-scope question should not have to
start the report over -- losing a half-completed report to a stray
question is a good way to make a resident give up and call back, which
costs the city more, not less.

The 911 case is handled upstream by the emergency layer. This layer
catches *non-emergency* out-of-scope material: requests about taxes,
billing disputes, legal advice, immigration, and similar.
"""

from __future__ import annotations

import re

_OUT_OF_SCOPE_PHRASES = (
    # 911 itself. If a caller says 911, they want an emergency line.
    "this is 911",
    "is this 911",
    "i need 911",
    "call 911",
    # Tax and assessment
    "tax bill",
    "property tax",
    "tax assessment",
    "tax office",
    "pay my tax",
    "tax due",
    # Utility billing disputes -- routed to the utility, not 311
    "electric bill",
    "water bill",
    "utility bill",
    "bill dispute",
    "billing dispute",
    # Legal advice
    "legal advice",
    "lawyer",
    "attorney",
    "lawsuit",
    "sue the city",
    "sue city",
    # Immigration and benefits
    "immigration",
    "visa",
    "deportation",
    "green card",
    # Housing / landlord disputes
    "landlord dispute",
    "my landlord",
    "eviction notice",
    # Licensing
    "business license",
    "permit for my",
    # Other lines
    "po box",
    "library card",
    "parking meter",
    "parking ticket",
    "pay a ticket",
    "citation",
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def detect_scope(transcript: str) -> bool:
    """Return True if the turn is outside what this line services."""
    if not transcript:
        return False
    normalized = _normalize(transcript)
    return any(phrase in normalized for phrase in _OUT_OF_SCOPE_PHRASES)
