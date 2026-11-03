"""PII redaction tests."""

from __future__ import annotations

import pytest

from src.guardrails.pii import redact_pii


class TestRedaction:
    @pytest.mark.parametrize(
        "transcript",
        [
            "my social security number is 123-45-6789",
            "it's 123 45 6789",
            "call me at 704-555-1234",
            "call 704 555 1234",
            "my email is jacob@example.com",
            "account number 123456789012",
            "routing 123456789",
        ],
    )
    def test_redacts_identifiers(self, transcript):
        redacted, notes = redact_pii(transcript)
        assert notes, f"no redaction note for: {transcript!r}"
        assert "REDACTED" in redacted

    def test_ssn_digits_do_not_survive(self):
        redacted, _ = redact_pii("my ssn is 123-45-6789")
        assert "123" not in redacted
        assert "6789" not in redacted

    def test_phone_digits_do_not_survive(self):
        redacted, _ = redact_pii("call me at 704-555-1234")
        assert "555" not in redacted
        assert "1234" not in redacted

    def test_email_does_not_survive(self):
        redacted, _ = redact_pii("email me at jacob@example.com")
        assert "jacob@example.com" not in redacted

    @pytest.mark.parametrize(
        "transcript",
        [
            "the pothole is on 123 South Boulevard",
            "my car plate is ABC123",
            "it happened at 11 PM",
            "there is a loud party next door",
        ],
    )
    def test_city_relevant_detail_is_not_redacted(self, transcript):
        """Redaction must not destroy the substance of a report.

        An address and a plate are what the case record is. Blanking
        them produces a record nobody can act on.
        """
        redacted, notes = redact_pii(transcript)
        assert not notes, f"over-redacted: {transcript!r} -> {redacted!r}"
        assert "REDACTED" not in redacted

    def test_empty_text_is_empty(self):
        redacted, notes = redact_pii("")
        assert redacted == ""
        assert notes == ()

    def test_notes_name_what_was_removed(self):
        _, notes = redact_pii(
            "my ssn is 123-45-6789 and my email is a@example.org"
        )
        assert "ssn" in notes
        assert "email" in notes


class TestNameDetectionIsDeliberatelyAbsent:
    def test_names_are_not_redacted(self):
        """Free-form names are not detected, on purpose.

        Name detection needs NER, and an NER detector that fires on
        ordinary English silently corrupts the record it is meant to
        protect. The limitation is recorded rather than hidden.
        """
        redacted, notes = redact_pii("my name is John Smith")
        assert "John Smith" in redacted
        assert not notes
