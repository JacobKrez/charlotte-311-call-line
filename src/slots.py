"""Slot extraction from a caller transcript.

This is the ASR-adjacent half of the system: turning a spoken string
into structured fields. It is deliberately rule-based rather than
model-based.

The reason is that the downstream consumers of these fields are
adversarial to the model. The report that gets written to the city's
record is built from what this module extracts. If extraction is done
by a language model, then the record's fidelity depends on a
probabilistic component, and the failure mode is a plausible-looking
wrong value in a legal filing. If extraction is done by rules, the
failure mode is "no value extracted", which the dialogue handles by
asking again.

That is a deliberate trade: rules cost recall on unusual phrasing and
buy determinism on the fields that end up in a public record. The
red-team corpus measures what that trade costs.

License plates get particular attention because speech recognition
mangles them constantly. A caller says "A B C one two three" and the
transcript arrives as "abc 123", "a b c 1 2 3", "ABC123", or "a b see
one two three". The extractor handles the homophone substitutions
callers actually use and the letter/number split that ASR introduces.
"""

from __future__ import annotations

import re

# Words callers substitute for letters when spelling a plate aloud.
_HOMOPHONES = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "see": "C",
    "sea": "C",
    "cee": "C",
    "jay": "J",
    "kay": "K",
    "el": "L",
    "elle": "L",
    "em": "M",
    "en": "N",
    "are": "R",
    "ar": "R",
    "you": "U",
    "why": "Y",
    "why not": "Y",
    "bee": "B",
    "be": "B",
    "dee": "D",
    "gee": "G",
    "jay": "J",
    "h": "H",
    "jay": "J",
}

# Filler words that show up between spoken plate characters.
#
# "a" is deliberately absent: it is a valid plate letter, and a caller
# spelling "A B C 1 2 3" says the letter A out loud. Dropping it as
# filler turns ABC123 into BC123.
_FILLERS = (
    "please",
    "uh",
    "um",
    "like",
    "okay",
    "yeah",
    "the",
    "is",
    "and",
    "my",
    "plate",
    "number",
    "license",
)

# A plate is 3-7 characters, alphanumeric, and in North Carolina the
# format is ABC-1234 (three letters, four digits). The regex is
# permissive on purpose: a strict ABC1234-only matcher would reject
# legitimate out-of-state plates the line still has to look up.
_PLATE_RE = re.compile(r"^[A-Z0-9]{3,7}$")

# Numbered street address at the start of a phrase.
_ADDRESS_RE = re.compile(
    r"\b(\d{1,6})\s+"
    r"(?:[NSEW]{1,2}\.?\s+)?"
    r"([A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*){0,3})\s+"
    r"(street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|"
    r"court|ct|way|place|pl|terrace|ter|circle|cir|highway|hwy|parkway|pkwy)\b",
    re.IGNORECASE,
)

# Time expressions worth capturing.
_CLOCK_RE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*"
    r"(am|pm|a\.m\.|p\.m\.|o'?clock)?\b",
    re.IGNORECASE,
)

_DAY_RE = re.compile(
    r"\b(yesterday|today|tonight)\b",
    re.IGNORECASE,
)

_TIME_OF_DAY_RE = re.compile(
    r"\b(morning|afternoon|evening|night|noon|midnight)\b",
    re.IGNORECASE,
)

_TIME_OF_DAY_WORDS = {
    "morning": "morning",
    "afternoon": "afternoon",
    "evening": "evening",
    "night": "night",
    "noon": "noon",
    "midnight": "midnight",
}

_DAY_WORDS = {
    "yesterday": "yesterday",
    "today": "today",
    "tonight": "tonight",
}


def normalize_for_plate(text: str) -> str:
    """Normalize a spoken plate fragment into candidate characters."""
    lowered = text.lower()
    cleaned = re.sub(r"[^a-z0-9\s]", " ", lowered)
    words = [w for w in cleaned.split() if w not in _FILLERS]
    mapped: list[str] = []
    for word in words:
        mapped.append(_HOMOPHONES.get(word, word.upper()))
    return " ".join(mapped)


def extract_plate(text: str) -> str | None:
    """Extract a license plate from a spoken fragment.

    Returns the normalized plate (uppercase, no separators) or None.

    Two passes, in order of decreasing confidence:

    1. A letter-run followed by a digit-run, allowing spaces between
       characters. This is the common "A B C one two three" case,
       where ASR keeps the spacing between spoken characters. The
       *longest* valid candidate wins, because a run of three letters
       and four digits is a better plate reading than the same run
       truncated after its first digit.
    2. A contiguous alphanumeric token of 3-7 characters. This catches
       the case where ASR wrote the plate straight through, as in
       "abc123".
    """
    if not text:
        return None

    # Pass 1: a maximal letter-run immediately followed by a maximal
    # digit-run, over the homophone-normalized token stream.
    #
    # The runs are found by scanning for the boundaries rather than by
    # accumulating incrementally, because a candidate only becomes
    # valid at the moment a digit follows a letter, and the digit run
    # that follows it may still be growing.
    tokens = normalize_for_plate(text).split()

    # Token kinds: "L" for a letter token, "D" for a digit token.
    kinds = [
        "L" if token.isalpha() else ("D" if token.isdigit() else "X")
        for token in tokens
    ]

    best: str | None = None
    index = 0
    while index < len(tokens):
        if kinds[index] != "L":
            index += 1
            continue

        # Consume the maximal letter run.
        letter_start = index
        while index < len(tokens) and kinds[index] == "L":
            index += 1
        letters = tokens[letter_start:index]

        # Consume the maximal digit run that immediately follows.
        digit_start = index
        while index < len(tokens) and kinds[index] == "D":
            index += 1
        digits = tokens[digit_start:index]

        if not digits:
            continue

        candidate = "".join(letters) + "".join(digits)
        # A plate is 3-7 characters. The letter and digit runs are each
        # bounded separately because no US plate has more than four of
        # either.
        if not (1 <= len(letters) <= 4 and 1 <= len(digits) <= 4):
            continue
        if not _PLATE_RE.match(candidate):
            continue
        if best is None or len(candidate) > len(best):
            best = candidate

    if best is not None:
        return best

    # Pass 2: contiguous token.
    for token in re.findall(r"\b[A-Za-z0-9]{3,7}\b", text):
        candidate = token.upper()
        if _PLATE_RE.match(candidate):
            # Reject pure alphabetic words that happen to be 3-7
            # letters: "towed", "noise", "please". A plate needs at
            # least one digit.
            if any(ch.isdigit() for ch in candidate):
                return candidate

    return None


def extract_address(text: str) -> str | None:
    """Extract a street address from a spoken phrase."""
    if not text:
        return None
    match = _ADDRESS_RE.search(text)
    if not match:
        return None
    number, street, suffix = match.group(1), match.group(2).strip(), match.group(3)
    return f"{number} {street} {suffix}"


def extract_time(text: str) -> str | None:
    """Extract a time-of-day phrase from a spoken phrase.

    Returns a normalized string, or None if nothing time-like was found.

    Resolution order:

    1. A clock time with a meridiem ("3:30 pm" -> "3:30 PM"). Most
       specific, so it wins.
    2. A day marker plus a time of day ("yesterday afternoon" ->
       "yesterday afternoon"). Both are kept, because the day is half
       the answer for a noise complaint.
    3. A time of day alone ("this morning" -> "morning").
    4. A day marker alone ("yesterday" -> "yesterday").
    5. A clock time without a meridiem ("at 8" -> "8:00"). The
       meridiem is deliberately not guessed: recording 8 PM when the
       caller meant 8 AM changes which night a citation covers, so the
       dialogue reads the value back and the caller confirms.
    """
    if not text:
        return None

    clock = _CLOCK_RE.search(text)
    day_match = _DAY_RE.search(text)
    tod_match = _TIME_OF_DAY_RE.search(text)

    day = _DAY_WORDS.get(day_match.group(1).lower()) if day_match else None
    time_of_day = (
        _TIME_OF_DAY_WORDS.get(tod_match.group(1).lower()) if tod_match else None
    )

    # 1. Clock with meridiem.
    if clock and clock.group(3):
        meridiem_raw = clock.group(3).replace(".", "").lower()
        if meridiem_raw in ("am", "pm"):
            hour = int(clock.group(1))
            minute = int(clock.group(2)) if clock.group(2) else 0
            if hour > 24 or minute > 59:
                return None
            return f"{hour}:{minute:02d} {meridiem_raw.upper()}"

    # 2. Day plus time of day.
    if day and time_of_day:
        return f"{day} {time_of_day}"

    # 3. Time of day alone.
    if time_of_day:
        return time_of_day

    # 4. Day alone.
    if day:
        return day

    # 5. Clock without a meridiem.
    if clock:
        hour = int(clock.group(1))
        minute = int(clock.group(2)) if clock.group(2) else 0
        if hour > 24 or minute > 59:
            return None
        return f"{hour}:{minute:02d}"

    return None


def extract_details(text: str) -> str:
    """Return the free-text details a caller gave, trimmed."""
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text).strip()
    # Strip a trailing confirmation word if the caller included it.
    cleaned = re.sub(
        r"\s+(?:is that correct|yeah|yes|that is correct|right)\b\.?$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()
