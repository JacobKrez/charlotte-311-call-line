"""Intent taxonomy for the 311 non-emergency line.

The taxonomy is deliberately small and closed. A public voice line that can
be talked into an unbounded set of actions is a liability, not a feature:
every intent added here is a new branch that has to be handled correctly
under adversarial audio, a bad transcript, and a 5-8 minute handle time.

``NON_EMERGENCY`` is the only class of request this line may service.
Anything that smells like an emergency is escalated, not served. That
split is enforced in :mod:`src.guardrails.emergency`, not here -- this
module only knows what a *non-emergency* request looks like.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Intent(str, Enum):
    """Closed set of intents the 311 line handles."""

    TOWED_VEHICLE = "towed_vehicle"
    POLICE_REPORT = "police_report"
    ANIMAL_CONTROL = "animal_control"
    ENVIRONMENTAL_COMPLAINT = "environmental_complaint"
    NOISE_COMPLAINT = "noise_complaint"
    PARKING_ISSUE = "parking_issue"
    GENERAL_INFORMATION = "general_information"
    EXIT = "exit"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IntentSpec:
    """How one intent is recognised, and what it is allowed to do."""

    intent: Intent
    label: str
    # Surface forms a caller may use. Kept lowercase; matching is done on
    # a normalized transcript.
    triggers: tuple[str, ...] = field(default_factory=tuple)
    # DTMF digit that selects this intent from the main menu.
    menu_digit: str | None = None
    # Intents that write a case record need these slots before filing.
    required_slots: tuple[str, ...] = field(default_factory=tuple)
    # Whether filing is permitted. Lookups are read-only; reports write.
    writes_record: bool = False


# A resident describing a towed car says many things. Matching is
# deliberately substring-based on a normalized transcript rather than a
# trained classifier: this prototype must behave identically on a laptop
# with no model weights and in CI with no network. The cost is recall on
# unseen phrasing, which the red-team corpus is designed to measure.
INTENT_SPECS: tuple[IntentSpec, ...] = (
    IntentSpec(
        intent=Intent.TOWED_VEHICLE,
        label="towed vehicle inquiry",
        triggers=(
            "towed",
            "tow",
            "impounded",
            "taken",
            "towing",
            "where is my car",
            "where is my vehicle",
        ),
        menu_digit="1",
        required_slots=("plate",),
        writes_record=False,
    ),
    IntentSpec(
        intent=Intent.POLICE_REPORT,
        label="police report",
        triggers=(
            "break in",
            "break-in",
            "breakin",
            "burglary",
            "vandalism",
            "car break",
            "stolen",
            "theft",
        ),
        menu_digit="2",
        required_slots=("location", "time", "details"),
        writes_record=True,
    ),
    IntentSpec(
        intent=Intent.ANIMAL_CONTROL,
        label="animal control",
        triggers=(
            "stray",
            "loose dog",
            "loose cat",
            "wild animal",
            "animal control",
            "biting dog",
        ),
        menu_digit="3",
        required_slots=("location", "time", "details"),
        writes_record=True,
    ),
    IntentSpec(
        intent=Intent.ENVIRONMENTAL_COMPLAINT,
        label="environmental complaint",
        triggers=(
            "dumping",
            "illegal dumping",
            "hazardous",
            "spill",
            "environmental",
            "pollution",
        ),
        menu_digit="4",
        required_slots=("location", "time", "details"),
        writes_record=True,
    ),
    IntentSpec(
        intent=Intent.NOISE_COMPLAINT,
        label="noise complaint",
        triggers=(
            "noise",
            "loud",
            "disturbance",
            "party",
            "loud music",
        ),
        menu_digit="5",
        required_slots=("location", "time", "details"),
        writes_record=True,
    ),
    IntentSpec(
        intent=Intent.PARKING_ISSUE,
        label="parking issue",
        triggers=(
            "parking",
            "blocked driveway",
            "blocked in",
            "illegal parking",
        ),
        menu_digit="6",
        required_slots=("location", "time", "details"),
        writes_record=True,
    ),
)

INTENT_BY_DIGIT: dict[str, Intent] = {
    spec.menu_digit: spec.intent for spec in INTENT_SPECS if spec.menu_digit
}

INTENT_BY_TRIGGER: dict[str, Intent] = {
    trigger: spec.intent for spec in INTENT_SPECS for trigger in spec.triggers
}

EXIT_TRIGGERS = (
    "exit",
    "end the call",
    "hang up",
    "goodbye",
    "quit",
    "stop the call",
)


def spec_for(intent: Intent) -> IntentSpec | None:
    """Return the spec for an intent, or None for EXIT/UNKNOWN/GENERAL."""
    for spec in INTENT_SPECS:
        if spec.intent is intent:
            return spec
    return None
