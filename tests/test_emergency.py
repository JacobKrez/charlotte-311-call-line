"""Emergency triage tests.

The escalation path is the single most important behavior in this
system. A caller who reports an active intrusion on a non-emergency
line must be escalated, and must not be filed as a routine report.

These tests cover the three escalation classes, the historical-versus-
current distinction, and the negative cases that would cause a false
escalation.
"""

from __future__ import annotations

import pytest

from src.guardrails.emergency import detect_emergency


class TestImmediateThreat:
    @pytest.mark.parametrize(
        "transcript",
        [
            "someone is breaking into my house right now",
            "there is a man in my house and he has a gun",
            "my house is on fire and my kids are still inside",
            "he pulled a gun on me",
            "I am trapped inside the burning building",
            "someone is hurting my daughter right now",
            "my child is missing and I don't know where she is",
            "I am going to kill myself",
        ],
    )
    def test_detects_emergency(self, transcript):
        signal = detect_emergency(transcript)
        assert signal is not None, f"missed emergency in: {transcript!r}"

    def test_classifies_active_intrusion(self):
        signal = detect_emergency("someone is breaking into my house right now")
        assert signal is not None
        assert signal.kind == "immediate_threat"

    def test_classifies_person_in_danger(self):
        signal = detect_emergency("someone is hurting my daughter right now")
        assert signal is not None
        assert signal.kind == "person_in_danger"


class TestHistoricalReportsAreNotEmergencies:
    """A report about the past is a report, not an emergency.

    These are the cases that would make the detector useless if it
    fired on them: every one of them is a legitimate non-emergency 311
    call.
    """

    @pytest.mark.parametrize(
        "transcript",
        [
            "someone broke into my car last night",
            "my house was broken into two weeks ago",
            "there was a break-in on our street yesterday morning",
            "I reported a burglary last month",
        ],
    )
    def test_past_event_is_not_an_emergency(self, transcript):
        assert detect_emergency(transcript) is None


class TestNegatives:
    @pytest.mark.parametrize(
        "transcript",
        [
            "my car was towed from the lot on Tryon street",
            "there is a loud party next door",
            "someone parked in my driveway",
            "a dog is loose in the park",
            "there is dumping behind the shopping center",
            "",
        ],
    )
    def test_ordinary_calls_are_not_emergencies(self, transcript):
        assert detect_emergency(transcript) is None

    def test_none_means_not_matched_not_safe(self):
        """The API contract: None means 'no pattern matched'.

        It does not mean 'this call is safe'. The red-team corpus
        exists precisely because that is the weaker claim, and it is
        the one the system can actually make.
        """
        assert detect_emergency("some phrasing nobody anticipated") is None


class TestNormalisation:
    def test_case_and_whitespace_insensitive(self):
        a = detect_emergency("Someone Is Breaking Into My House")
        b = detect_emergency("  someone   is  breaking into my house  ")
        assert a is not None
        assert b is not None
        assert a.kind == b.kind
