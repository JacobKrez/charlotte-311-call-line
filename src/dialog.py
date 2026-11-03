"""The 311 call dialogue state machine.

The machine is explicit and finite. Every state is a named enum value,
every transition is a named method, and there is no path from a state
that does not exist. This is the same choice the FoodPath case study
made for its eligibility conversation, and for the same reason: a
public-facing agent that can be talked into an unexpected state is a
liability, and a state machine you can read top to bottom is one you
can reason about.

The states:

``GREETING``
    First turn. The caller hears what the line does and does not do.
``MENU``
    The caller has been asked for an intent.
``TOW_PLATE``
    A towed-vehicle lookup is in progress; we need the plate.
``TOW_CONFIRM``
    We read the plate back; the caller confirms or corrects it.
``REPORT_KIND``
    A report is in progress; we confirm which kind of report.
``REPORT_LOCATION``
    We need where the issue is.
``REPORT_LOCATION_CONFIRM``
    Read-back of the location.
``REPORT_TIME``
    We need when it happened.
``REPORT_TIME_CONFIRM``
    Read-back of the time.
``REPORT_DETAILS``
    We need the description.
``REPORT_DETAILS_CONFIRM``
    Read-back of the description; the next turn files the record.
``ESCALATED``
    Terminal. The call was handed to a human.
``ENDED``
    Terminal. The caller hung up.

Two rules govern every transition, and both exist because the
alternative is a false record on the city's system:

1. **Nothing is written until the caller confirms it.** Every slot is
   read back before it is stored. A caller who says "just file it" is
   refused by the injection guardrail before it reaches this machine.
2. **A confirmation can be revoked.** A caller who corrects the
   location goes back to the location prompt rather than forward. The
   record is not assembled from the caller's last word.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .guardrails import Disposition, evaluate, prepare_for_log
from .intents import (
    EXIT_TRIGGERS,
    INTENT_BY_DIGIT,
    INTENT_BY_TRIGGER,
    Intent,
    spec_for,
)
from .slots import extract_address, extract_details, extract_plate, extract_time


class State(str, Enum):
    GREETING = "greeting"
    MENU = "menu"
    TOW_PLATE = "tow_plate"
    TOW_CONFIRM = "tow_confirm"
    REPORT_KIND = "report_kind"
    REPORT_LOCATION = "report_location"
    REPORT_LOCATION_CONFIRM = "report_location_confirm"
    REPORT_TIME = "report_time"
    REPORT_TIME_CONFIRM = "report_time_confirm"
    REPORT_DETAILS = "report_details"
    REPORT_DETAILS_CONFIRM = "report_details_confirm"
    ESCALATED = "escalated"
    ENDED = "ended"


MENU_PROMPT = (
    "Say one for a towed vehicle inquiry, two for a police report, three "
    "for animal control, four for an environmental complaint, five for a "
    "noise complaint, six for a parking issue, or describe your issue."
)

GREETING = (
    "You've reached the 311 non-emergency line. This line handles "
    "non-emergency city services. If this is an emergency, hang up and "
    "call 911. "
    + MENU_PROMPT
)

TURN_LIMIT_MESSAGE = (
    "I'm having trouble understanding. Let me connect you with a person. "
    "Please hold."
)


@dataclass
class CallContext:
    """Everything collected during one call."""

    intent: Intent | None = None
    plate: str | None = None
    location: str | None = None
    time_of_incident: str | None = None
    details: str | None = None
    # Counters. Each caps how long the machine will stay in a stuck
    # state before handing off to a human.
    menu_attempts: int = 0
    plate_attempts: int = 0
    confirm_attempts: int = 0
    location_attempts: int = 0
    time_attempts: int = 0
    # Every turn, for the transcript log. Redacted before storage.
    turns: list[dict] = field(default_factory=list)
    # The record that was filed, if any.
    filed_record: dict | None = None
    # Set when the guardrail chain escalated.
    escalated_reason: str | None = None

    def clear_slots(self) -> None:
        """Reset collected fields, keeping counters and log."""
        self.intent = None
        self.plate = None
        self.location = None
        self.time_of_incident = None
        self.details = None


@dataclass
class Turn:
    """The result of processing one caller turn."""

    state: State
    # What the agent says back.
    response: str
    # Whether the turn ended the call.
    terminal: bool = False
    # Whether the turn escalated to a human.
    escalated: bool = False
    # Guardrail disposition for this turn.
    disposition: Disposition = Disposition.CONTINUE
    # Notes for the audit log.
    notes: tuple[str, ...] = ()
    # A record was filed on this turn.
    record_filed: dict | None = None


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _is_affirmative(text: str) -> bool:
    normalized = _normalize(text)
    return normalized in (
        "yes",
        "yeah",
        "yep",
        "yup",
        "correct",
        "that's right",
        "thats right",
        "that is right",
        "right",
        "ok",
        "okay",
        "sure",
        "yes please",
        "yes that's correct",
    )


def _is_negative(text: str) -> bool:
    normalized = _normalize(text)
    return normalized in (
        "no",
        "nope",
        "not right",
        "that's wrong",
        "thats wrong",
        "incorrect",
        "no that's wrong",
        "wrong",
    )


def _is_repeat(text: str) -> bool:
    normalized = _normalize(text)
    return "repeat" in normalized or "say that again" in normalized


class Dialog:
    """The 311 dialogue state machine.

    Parameters
    ----------
    on_file:
        Callable invoked with the assembled record when the caller
        confirms a report. Injected rather than imported so the state
        machine has no side effects of its own and can be tested without
        a database.
    on_escalate:
        Callable invoked when the call escalates to a human. Receives a
        reason string.
    """

    def __init__(
        self,
        *,
        on_file: Callable[[dict], dict] | None = None,
        on_escalate: Callable[[str], None] | None = None,
        on_audit: Callable[..., None] | None = None,
    ) -> None:
        self.state = State.GREETING
        self.context = CallContext()
        self._on_file = on_file or self._default_file
        self._on_escalate = on_escalate or (lambda reason: None)
        # Audit logging lives here rather than in the runner, so that
        # any caller of process() gets an audit trail. A runner that
        # forgot to log would silently produce an unlogged call.
        self._on_audit = on_audit or (lambda **kwargs: None)

    @staticmethod
    def _default_file(record: dict) -> dict:
        """In-memory file handler used when no handler is injected."""
        return {"report_id": str(uuid.uuid4()), "status": "Filed", **record}

    # -- helpers ---------------------------------------------------------

    def _record_turn(self, transcript: str, disposition: Disposition, notes: tuple[str, ...]) -> None:
        """Append a redacted turn to the transcript log.

        Redaction happens at the single point where a transcript becomes
        a stored row, so a new code path cannot store an unredacted
        transcript by accident.
        """
        redacted, pii_notes = prepare_for_log(transcript)
        all_notes = list(notes) + list(pii_notes)
        self.context.turns.append(
            {
                "state": self.state.value,
                "transcript": redacted,
                "disposition": disposition.value,
                "notes": all_notes,
            }
        )
        self._on_audit(
            state=self.state.value,
            disposition=disposition.value,
            transcript=transcript,
            notes=notes,
        )

    def _classify_intent(self, text: str) -> Intent:
        """Classify a caller turn into an intent.

        Menu digits win over keyword matching. A caller who pressed 1
        meant the towed-vehicle path even if the ASR transcript happens
        to contain a word from another trigger.
        """
        normalized = _normalize(text)

        # A leading digit, or a digit spoken as a word.
        digit_words = {
            "one": "1",
            "two": "2",
            "three": "3",
            "four": "4",
            "five": "5",
            "six": "6",
        }
        tokens = normalized.split()
        if tokens:
            first = tokens[0]
            candidate = digit_words.get(first, first)
            if candidate in INTENT_BY_DIGIT:
                return INTENT_BY_DIGIT[candidate]

        # Keyword triggers, longest first so "illegal dumping" wins over
        # "dumping".
        for trigger in sorted(INTENT_BY_TRIGGER, key=len, reverse=True):
            if trigger in normalized:
                return INTENT_BY_TRIGGER[trigger]

        return Intent.UNKNOWN

    def _lookup_tow(self, plate: str) -> dict:
        """Look up a towed vehicle.

        This prototype does not connect to the tow yard's system. The
        lookup is a stub that returns a deterministic "not found" so the
        dialogue path is exercised end to end without inventing records
        for vehicles that may not exist.
        """
        return {"error": "I don't have a record for that plate."}

    # -- the turn handler ------------------------------------------------

    def process(self, transcript: str) -> Turn:
        """Process one caller turn and return the agent's response."""
        if self.state in (State.ESCALATED, State.ENDED):
            return Turn(
                state=self.state,
                response="",
                terminal=True,
                escalated=self.state is State.ESCALATED,
                notes=("call_already_terminal",),
            )

        text = transcript.strip()
        if not text:
            self._record_turn(text, Disposition.CONTINUE, ("empty_transcript",))
            return Turn(
                state=self.state,
                response="I didn't hear anything. Please speak again.",
                notes=("empty_transcript",),
            )

        # Guardrail chain first. Emergency short-circuits everything.
        verdict = evaluate(text)
        self._record_turn(text, verdict.disposition, verdict.notes)

        if verdict.disposition is Disposition.ESCALATE:
            self.state = State.ESCALATED
            reason = verdict.notes[0] if verdict.notes else "emergency"
            self.context.escalated_reason = reason
            self._on_escalate(reason)
            return Turn(
                state=self.state,
                response=verdict.message,
                terminal=True,
                escalated=True,
                disposition=verdict.disposition,
                notes=verdict.notes,
            )

        if verdict.disposition is Disposition.REFUSE:
            return Turn(
                state=self.state,
                response=verdict.message,
                disposition=verdict.disposition,
                notes=verdict.notes,
            )

        if verdict.disposition is Disposition.REDIRECT:
            return Turn(
                state=self.state,
                response=verdict.message,
                disposition=verdict.disposition,
                notes=verdict.notes,
            )

        # Guardrails are clear. Now the state machine.
        handler = getattr(self, f"_state_{self.state.value}")
        return handler(text)

    # -- states ----------------------------------------------------------

    def _state_greeting(self, text: str) -> Turn:
        self.state = State.MENU
        return Turn(state=self.state, response=GREETING)

    def _state_menu(self, text: str) -> Turn:
        normalized = _normalize(text)

        if any(trigger in normalized for trigger in EXIT_TRIGGERS):
            self.state = State.ENDED
            return Turn(
                state=self.state,
                response="Thank you for calling. Goodbye.",
                terminal=True,
                notes=("caller_ended",),
            )

        intent = self._classify_intent(text)

        if intent is Intent.TOWED_VEHICLE:
            self.context.intent = intent
            self.state = State.TOW_PLATE
            return Turn(
                state=self.state,
                response="Please say the license plate number, like 'A B C one two three'.",
            )

        spec = spec_for(intent)
        if spec is not None and spec.writes_record:
            self.context.intent = intent
            self.state = State.REPORT_KIND
            return Turn(
                state=self.state,
                response=(
                    f"You want to file a {spec.label}. Is that correct? "
                    "Say yes or no."
                ),
            )

        # Not understood. Give the caller a few tries, then hand off.
        self.context.menu_attempts += 1
        if self.context.menu_attempts >= 3:
            self.state = State.ESCALATED
            self.context.escalated_reason = "menu_repeated_failure"
            self._on_escalate(self.context.escalated_reason)
            return Turn(
                state=self.state,
                response=TURN_LIMIT_MESSAGE,
                terminal=True,
                escalated=True,
                notes=("menu_repeated_failure",),
            )

        return Turn(
            state=self.state,
            response=MENU_PROMPT,
            notes=("intent_not_understood",),
        )

    def _state_tow_plate(self, text: str) -> Turn:
        plate = extract_plate(text)
        if plate:
            self.context.plate = plate
            self.state = State.TOW_CONFIRM
            return Turn(
                state=self.state,
                response=(
                    f"You said license plate {plate}. Is that correct? "
                    "Say yes, no, or repeat."
                ),
            )

        self.context.plate_attempts += 1
        if self.context.plate_attempts >= 3:
            self.state = State.MENU
            self.context.plate_attempts = 0
            return Turn(
                state=self.state,
                response=(
                    "I'm having trouble understanding the plate. Let's start "
                    "over. Please describe your issue."
                ),
                notes=("plate_repeated_failure",),
            )

        return Turn(
            state=self.state,
            response="Please say the license plate number, like 'A B C one two three'.",
            notes=("plate_not_understood",),
        )

    def _state_tow_confirm(self, text: str) -> Turn:
        if _is_affirmative(text):
            result = self._lookup_tow(self.context.plate or "")
            if "error" in result:
                self.state = State.MENU
                self.context.clear_slots()
                return Turn(
                    state=self.state,
                    response=f"{result['error']} {MENU_PROMPT}",
                    notes=("tow_not_found",),
                )
            self.state = State.MENU
            self.context.clear_slots()
            return Turn(
                state=self.state,
                response=(
                    f"Your {result.get('color', '')} {result.get('make', '')} "
                    f"{result.get('model', '')} is at {result.get('tow_lot_name', '')}. "
                    f"Call {result.get('tow_lot_phone', '')} for details. "
                    f"{MENU_PROMPT}"
                ),
            )

        if _is_repeat(text):
            return Turn(
                state=self.state,
                response=(
                    f"The license plate is {self.context.plate}. Say yes to "
                    "continue or no to correct it."
                ),
            )

        if _is_negative(text):
            self.state = State.TOW_PLATE
            self.context.plate_attempts = 0
            return Turn(
                state=self.state,
                response="Please say the correct license plate number.",
            )

        self.context.confirm_attempts += 1
        if self.context.confirm_attempts >= 3:
            self.state = State.MENU
            self.context.clear_slots()
            self.context.confirm_attempts = 0
            return Turn(
                state=self.state,
                response=(
                    "I'm having trouble confirming. Let's start over. Please "
                    "describe your issue."
                ),
                notes=("confirm_repeated_failure",),
            )

        return Turn(
            state=self.state,
            response="Please say the correct license plate number.",
            notes=("confirmation_not_understood",),
        )

    def _state_report_kind(self, text: str) -> Turn:
        if _is_affirmative(text):
            self.state = State.REPORT_LOCATION
            return Turn(
                state=self.state,
                response="Please say the location of the issue, such as the street address.",
            )

        # Anything else, including "no", restarts the intent selection.
        # But if the caller named a different intent, take it.
        intent = self._classify_intent(text)
        spec = spec_for(intent)
        if spec is not None and spec.writes_record:
            self.context.intent = intent
            return Turn(
                state=self.state,
                response=f"You want to file a {spec.label}. Is that correct?",
            )

        self.state = State.MENU
        self.context.clear_slots()
        return Turn(state=self.state, response=MENU_PROMPT)

    def _state_report_location(self, text: str) -> Turn:
        location = extract_address(text)
        if not location:
            self.context.location_attempts += 1
            if self.context.location_attempts >= 3:
                self.state = State.MENU
                self.context.clear_slots()
                self.context.location_attempts = 0
                return Turn(
                    state=self.state,
                    response=(
                        "I'm having trouble with the address. Let's start over. "
                        "Please describe your issue."
                    ),
                    notes=("location_repeated_failure",),
                )
            return Turn(
                state=self.state,
                response="Please say the street address of the issue.",
                notes=("location_not_understood",),
            )

        self.context.location = location
        self.state = State.REPORT_LOCATION_CONFIRM
        return Turn(
            state=self.state,
            response=f"You said {location}. Is that correct? Say yes or no.",
        )

    def _state_report_location_confirm(self, text: str) -> Turn:
        if _is_affirmative(text):
            self.state = State.REPORT_TIME
            return Turn(
                state=self.state,
                response="Please say the approximate time of the issue.",
            )

        if _is_negative(text):
            self.state = State.REPORT_LOCATION
            self.context.location_attempts = 0
            return Turn(
                state=self.state,
                response="Please say the correct street address.",
            )

        return Turn(
            state=self.state,
            response="Please say the correct street address.",
            notes=("confirmation_not_understood",),
        )

    def _state_report_time(self, text: str) -> Turn:
        when = extract_time(text)
        if not when:
            self.context.time_attempts += 1
            if self.context.time_attempts >= 3:
                self.state = State.MENU
                self.context.clear_slots()
                self.context.time_attempts = 0
                return Turn(
                    state=self.state,
                    response=(
                        "I'm having trouble with the time. Let's start over. "
                        "Please describe your issue."
                    ),
                    notes=("time_repeated_failure",),
                )
            return Turn(
                state=self.state,
                response="Please say the approximate time, such as 'this morning at 8 AM'.",
                notes=("time_not_understood",),
            )

        self.context.time_of_incident = when
        self.state = State.REPORT_TIME_CONFIRM
        return Turn(
            state=self.state,
            response=f"You said {when}. Is that correct? Say yes or no.",
        )

    def _state_report_time_confirm(self, text: str) -> Turn:
        if _is_affirmative(text):
            self.state = State.REPORT_DETAILS
            spec = spec_for(self.context.intent) if self.context.intent else None
            label = spec.label if spec else "report"
            return Turn(
                state=self.state,
                response=f"Please describe the {label}, including any relevant details.",
            )

        if _is_negative(text):
            self.state = State.REPORT_TIME
            self.context.time_attempts = 0
            return Turn(
                state=self.state,
                response="Please say the correct time.",
            )

        return Turn(
            state=self.state,
            response="Please say the correct time.",
            notes=("confirmation_not_understood",),
        )

    def _state_report_details(self, text: str) -> Turn:
        details = extract_details(text)
        if not details:
            return Turn(
                state=self.state,
                response="Please describe the issue in a few words.",
                notes=("details_empty",),
            )

        self.context.details = details
        self.state = State.REPORT_DETAILS_CONFIRM
        return Turn(
            state=self.state,
            response=(
                f"You said: {details}. Is that correct? Say yes to file the "
                "report or no to revise."
            ),
        )

    def _state_report_details_confirm(self, text: str) -> Turn:
        if _is_affirmative(text):
            spec = spec_for(self.context.intent) if self.context.intent else None
            record = {
                "incident_type": spec.intent.value if spec else "unknown",
                "label": spec.label if spec else "report",
                "location": self.context.location,
                "time": self.context.time_of_incident,
                "details": self.context.details,
            }
            filed = self._on_file(record)
            self.context.filed_record = filed
            self.state = State.MENU
            self.context.clear_slots()
            return Turn(
                state=self.state,
                response=(
                    f"Your {record['label']} has been filed. Your reference "
                    f"number is {filed.get('report_id', 'unknown')}. "
                    "You'll be contacted for follow-up. "
                    + MENU_PROMPT
                ),
                record_filed=filed,
            )

        self.state = State.REPORT_DETAILS
        spec = spec_for(self.context.intent) if self.context.intent else None
        label = spec.label if spec else "report"
        return Turn(
            state=self.state,
            response=f"Please provide the details again for the {label}.",
        )
