"""Slot extraction tests.

Slot extraction is where a wrong value becomes a wrong record, so
these tests are mostly about what the extractor refuses to guess.
"""

from __future__ import annotations

import pytest

from src.slots import (
    extract_address,
    extract_details,
    extract_plate,
    extract_time,
)


class TestPlateExtraction:
    @pytest.mark.parametrize(
        "transcript",
        [
            "ABC123",
            "abc 123",
            "A B C one two three",
            "a b c 1 2 3",
            "ABC 123",
            "a b see one two three",
            "ABC 1234",
            "abc1234",
            "my plate is abc123",
        ],
    )
    def test_extracts_plate(self, transcript):
        plate = extract_plate(transcript)
        assert plate is not None, f"missed plate in: {transcript!r}"
        assert plate.isalnum()
        assert 3 <= len(plate) <= 7

    def test_normalizes_spaced_letters_and_numbers(self):
        assert extract_plate("a b c one two three") == "ABC123"

    def test_handles_homophones(self):
        assert extract_plate("a b see one two three") == "ABC123"

    @pytest.mark.parametrize(
        "transcript",
        [
            "",
            "hello",
            "towed",
            "noise complaint",
            "my name is jacob",
        ],
    )
    def test_does_not_invent_a_plate(self, transcript):
        assert extract_plate(transcript) is None

    def test_rejects_purely_alphabetic_words(self):
        """A 3-7 letter word is not a plate.

        Without the digit requirement, 'towed' and 'please' would be
        extracted as plates and the caller would be told their car is a
        Toyota Camry.
        """
        assert extract_plate("towed") is None
        assert extract_plate("please") is None


class TestAddressExtraction:
    @pytest.mark.parametrize(
        "transcript",
        [
            "123 South Boulevard",
            "it's on 456 Tryon Street",
            "789 North Davidson Street",
            "at 1000 East Trade Street",
        ],
    )
    def test_extracts_address(self, transcript):
        address = extract_address(transcript)
        assert address is not None, f"missed address in: {transcript!r}"
        assert address[0].isdigit()

    @pytest.mark.parametrize(
        "transcript",
        ["", "down the street", "near the park", "my house"],
    )
    def test_does_not_invent_an_address(self, transcript):
        assert extract_address(transcript) is None


class TestTimeExtraction:
    @pytest.mark.parametrize(
        "transcript,expected",
        [
            ("at 8 AM", "8:00 AM"),
            ("around 3:30 pm", "3:30 PM"),
            ("this morning", "morning"),
            ("yesterday afternoon", "yesterday afternoon"),
            ("at 11 o'clock", "11:00"),
            ("tonight", "tonight"),
        ],
    )
    def test_extracts_time(self, transcript, expected):
        assert extract_time(transcript) == expected

    def test_bare_hour_is_captured_without_a_meridiem(self):
        """'at 8' is ambiguous, so it is captured and confirmed.

        Guessing 8 PM when the caller meant 8 AM changes which night a
        noise complaint covers. The dialogue reads it back.
        """
        result = extract_time("it happened at 8")
        assert result is not None
        assert "AM" not in result and "PM" not in result

    @pytest.mark.parametrize("transcript", ["", "a while ago", "recently"])
    def test_does_not_invent_a_time(self, transcript):
        assert extract_time(transcript) is None


class TestDetailsExtraction:
    def test_strips_trailing_confirmation(self):
        assert extract_details("loud music at 2am is that correct") == "loud music at 2am"

    def test_collapses_whitespace(self):
        assert extract_details("lots   of   trash") == "lots of trash"

    def test_empty_is_empty(self):
        assert extract_details("") == ""
