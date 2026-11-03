#!/usr/bin/env python3
"""Quality gate: one command that says green or red.

Runs every gate in sequence and prints a single table plus an overall
PASS/FAIL, exiting non-zero on any failure. The full list of gates and
the rationale for each threshold lives in ``QUALITY_BAR.md`` at the repo
root.

This is the loop's definition of done. Nothing ships past a red gate.

Usage
-----
    python3 scripts/gate.py            # full gate
    python3 scripts/gate.py --help     # usage
    python3 scripts/gate.py --quiet    # one-line summary only
    python3 scripts/gate.py --only G4  # run a single gate

Exit codes
----------
    0   every gate passed
    1   at least one gate failed
    2   the gate script itself errored
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── Gate implementations ────────────────────────────────────────────


def gate_g1_pytest() -> tuple[bool, str]:
    """G1: the pytest suite passes with no traceback."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    if "Traceback" in output:
        return False, "pytest produced a Traceback"
    if result.returncode != 0:
        tail = [line for line in output.strip().splitlines() if line.strip()][-3:]
        return False, "; ".join(tail)
    passed = re.search(r"(\d+) passed", output)
    return True, f"{passed.group(1) if passed else '?'} passed"


def gate_g2_redteam() -> tuple[bool, str]:
    """G2: the red-team corpus meets its per-category thresholds."""
    result = subprocess.run(
        [sys.executable, "redteam/run_redteam.py", "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode == 2:
        return False, "red-team harness errored"
    try:
        import json

        summary = json.loads(result.stdout)
    except Exception as exc:  # pragma: no cover - harness failure path
        return False, f"could not parse red-team output: {exc}"

    if not summary.get("all_thresholds_met"):
        failed = [
            f"{category} {bucket['rate']:.0%} < {bucket['threshold']:.0%}"
            for category, bucket in summary.get("categories", {}).items()
            if not bucket.get("meets_threshold")
        ]
        return False, "; ".join(failed)
    return True, (
        f"{summary['passed']}/{summary['total']} cases, all categories at threshold"
    )


def gate_g3_no_secrets() -> tuple[bool, str]:
    """G3: no secrets committed."""
    patterns = [
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"AKIA[0-9A-Z]{16}",
        r"ASIA[0-9A-Z]{16}",
        r"gh[pousr]_[A-Za-z0-9]{20,}",
        r"sk-[A-Za-z0-9]{20,}",
        r"xox[baprs]-[A-Za-z0-9-]{10,}",
        r"AIza[0-9A-Za-z_\-]{35}",
        r'"private_key"\s*:\s*"[^"]+"',
        r"password\s*=\s*['\"][^'\"]{3,}['\"]",
        r"api[_-]?key\s*=\s*['\"][^'\"]{8,}['\"]",
    ]
    hits = _scan(patterns)
    if hits:
        return False, f"{len(hits)} secret-like pattern(s): {hits[:3]}"
    return True, "no secret patterns found"


# Synthetic fixture values that are permitted to appear in the
# repository.
#
# The redactor has to be tested against personal-information *shapes*,
# so the test corpus necessarily contains strings shaped like an SSN or
# a phone number. Those values have to be ones that cannot be a real
# person's.
#
# Every value listed here is documented as synthetic and is chosen so
# that it cannot identify anyone:
#
#   123-45-6789   SSN-shaped. Area number 123 was never issued by the
#                 Social Security Administration (the invalid ranges
#                 are 000, 666, and 900-999), so this cannot be a
#                 real-assigned number.
#   704-555-1234  Phone-shaped. 555-01xx is the reserved fictional
#                 number range, and no real subscriber holds a 555
#                 number.
#   123456789012  Account-shaped. A bare run of digits with no
#                 checksum, used only to exercise the redactor.
#   123456789     Routing-shaped. Same, no checksum.
#
# A personal-information-shaped string that is NOT in this list fails
# G4. That is the point: adding a new fixture value requires adding a
# line here, which is a reviewable act.
ALLOWED_SYNTHETIC_VALUES = (
    "123-45-6789",
    "704-555-1234",
    "123456789012",
    "123456789",
)

# Files that legitimately contain the patterns they are checking for.
#
# scripts/gate.py contains the secret and personal-data regexes as
# literal strings, and QUALITY_BAR.md quotes the thresholds. A check
# that fails on the definition of the check is not a check.
SELF_REFERENTIAL_FILES = frozenset(
    {
        "scripts/gate.py",
        "QUALITY_BAR.md",
    }
)


def gate_g4_no_personal_data() -> tuple[bool, str]:
    """G4: no personal data outside the synthetic fixtures."""
    patterns = [
        r"\b\d{3}-\d{2}-\d{4}\b",
        r"\b\d{3}\.\d{2}\.\d{4}\b",
        r"\(\d{3}\)\s?\d{3}-\d{4}",
        r"\b\d{3}-\d{3}-\d{4}\b",
    ]
    hits = _scan(patterns, skip_files=SELF_REFERENTIAL_FILES)
    unexpected = [
        hit for hit in hits if not _is_allowed_synthetic(hit)
    ]
    if unexpected:
        return False, f"personal-data-shaped strings found: {unexpected[:3]}"
    # Email addresses must use the RFC 2606 reserved example domains.
    email_hits = _scan(
        [r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"], skip_files=SELF_REFERENTIAL_FILES
    )
    allowed = ("example.com", "example.org", "example.net", "localhost")
    bad_emails = [
        hit
        for hit in email_hits
        if not any(domain in hit.split(":")[-1] for domain in allowed)
    ]
    if bad_emails:
        return False, f"non-example email addresses: {bad_emails[:3]}"
    return True, f"no personal data outside {len(ALLOWED_SYNTHETIC_VALUES)} synthetic fixtures"


def _is_allowed_synthetic(hit: str) -> bool:
    """True if the flagged line contains only allowlisted synthetic values.

    A line qualifies only if every personal-information-shaped token on
    it is an allowlisted value. A line that mixes an allowlisted
    fixture with an unrecognized number still fails, because the
    unrecognized number is the one that matters.
    """
    parts = hit.split(":")
    if len(parts) < 3:
        return False
    relative, line_number = parts[0], parts[1]
    try:
        path = REPO_ROOT / relative
        lines = path.read_text(encoding="utf-8").splitlines()
        text = lines[int(line_number) - 1]
    except (OSError, ValueError, IndexError, UnicodeDecodeError):
        return False

    # Remove the allowlisted values, then check whether any
    # personal-information-shaped token remains.
    residue = text
    for value in ALLOWED_SYNTHETIC_VALUES:
        residue = residue.replace(value, "")
    remaining = re.findall(
        r"\b\d{3}-\d{2}-\d{4}\b|\b\d{3}\.\d{2}\.\d{4}\b"
        r"|\(\d{3}\)\s?\d{3}-\d{4}|\b\d{3}-\d{3}-\d{4}\b",
        residue,
    )
    return not remaining


def gate_g5_guardrail_order() -> tuple[bool, str]:
    """G5: the guardrail chain order is asserted by a test."""
    test_file = REPO_ROOT / "tests" / "test_guardrail_order.py"
    if not test_file.exists():
        return False, "tests/test_guardrail_order.py is missing"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_guardrail_order.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False, "guardrail ordering tests failed"
    return True, "emergency -> injection -> scope order asserted"


def gate_g6_no_writes_before_confirmation() -> tuple[bool, str]:
    """G6: nothing is filed before the caller confirms."""
    test_file = REPO_ROOT / "tests" / "test_dialog.py"
    if not test_file.exists():
        return False, "tests/test_dialog.py is missing"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_dialog.py::TestReportFiling",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False, "confirmation-integrity tests failed"
    return True, "no writes before confirmation"


def gate_g7_required_files() -> tuple[bool, str]:
    """G7: required files and README sections are present."""
    required_files = [
        "README.md",
        "ARCHITECTURE.md",
        "EVALUATION.md",
        "QUALITY_BAR.md",
        "LICENSE",
        "requirements.txt",
        ".gitignore",
        "src/__init__.py",
        "src/audio.py",
        "src/cli.py",
        "src/dialog.py",
        "src/guardrails/__init__.py",
        "src/guardrails/emergency.py",
        "src/guardrails/injection.py",
        "src/guardrails/pii.py",
        "src/guardrails/scope.py",
        "src/intents.py",
        "src/slots.py",
        "src/store.py",
        "redteam/run_redteam.py",
        "redteam/attack_cases.jsonl",
        "scripts/gate.py",
    ]
    missing = [name for name in required_files if not (REPO_ROOT / name).exists()]
    if missing:
        return False, f"missing files: {missing[:5]}"

    readme = (REPO_ROOT / "README.md").read_text()
    required_sections = [
        "## What this is",
        "## What we cut, and why",
        "## Quickstart",
        "## Architecture",
        "## Guardrails",
        "## Known limitations",
        "## Production roadmap",
        "## Repository layout",
        "## Safety",
    ]
    absent = [section for section in required_sections if section not in readme]
    if absent:
        return False, f"README missing sections: {absent[:4]}"
    return True, f"{len(required_files)} files, {len(required_sections)} README sections"


def gate_g8_no_absolute_paths() -> tuple[bool, str]:
    """G8: no absolute developer paths in tracked files."""
    hits = _scan(
        [r"/Users/[A-Za-z0-9._-]+/", r"[A-Z]:\\\\Users\\\\"],
        skip_files=SELF_REFERENTIAL_FILES,
    )
    if hits:
        return False, f"absolute paths found: {hits[:3]}"
    return True, "no absolute paths"


# ── Helpers ─────────────────────────────────────────────────────────


def _tracked_files() -> list[Path]:
    """Every file the gate should inspect.

    Text files only. Binary files are skipped by extension, and hidden
    directories like .git are excluded.
    """
    skip_dirs = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules"}
    skip_suffixes = {
        ".pyc", ".png", ".jpg", ".jpeg", ".gif", ".wav", ".mp3", ".pdf",
        ".zip", ".gz", ".db", ".sqlite",
    }
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.relative_to(REPO_ROOT).parts):
            continue
        if path.suffix.lower() in skip_suffixes:
            continue
        files.append(path)
    return files


def _scan(patterns: list[str], skip_files: frozenset[str] = frozenset()) -> list[str]:
    """Scan repository files for the given regex patterns.

    Returns a list of human-readable hit descriptions of the form
    ``relative/path:line:matched_text``.

    The matched text is included because some checks need to inspect
    what was found, not only where. A caller that only needs the
    location can ignore the third field.

    Parameters
    ----------
    patterns:
        The regex patterns to look for.
    skip_files:
        Repository-relative paths to exclude. Used for the files that
        define the patterns themselves.
    """
    hits: list[str] = []
    compiled = [re.compile(p) for p in patterns]
    for path in _tracked_files():
        relative = str(path.relative_to(REPO_ROOT))
        if relative in skip_files:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for index, line in enumerate(text.splitlines(), start=1):
            for pattern in compiled:
                match = pattern.search(line)
                if match:
                    hits.append(f"{relative}:{index}:{match.group(0)}")
                    break
    return hits


GATES = [
    ("G1", "pytest suite green", gate_g1_pytest),
    ("G2", "red-team thresholds", gate_g2_redteam),
    ("G3", "no secrets committed", gate_g3_no_secrets),
    ("G4", "no personal data", gate_g4_no_personal_data),
    ("G5", "guardrail chain order", gate_g5_guardrail_order),
    ("G6", "no writes before confirmation", gate_g6_no_writes_before_confirmation),
    ("G7", "required files and sections", gate_g7_required_files),
    ("G8", "no absolute developer paths", gate_g8_no_absolute_paths),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 scripts/gate.py",
        description="Run the quality gate. Exit 0 green, 1 red.",
    )
    parser.add_argument("--quiet", action="store_true", help="One-line summary only.")
    parser.add_argument("--only", help="Run a single gate by id, e.g. G4.")
    args = parser.parse_args(argv)

    selected = [g for g in GATES if not args.only or g[0] == args.only.upper()]
    if not selected:
        print(f"no such gate: {args.only}", file=sys.stderr)
        return 2

    results: list[tuple[str, str, bool, str]] = []
    for gate_id, label, fn in selected:
        try:
            passed, detail = fn()
        except Exception as exc:  # pragma: no cover - gate failure path
            passed, detail = False, f"gate errored: {exc}"
        results.append((gate_id, label, passed, detail))

    failed = [r for r in results if not r[2]]

    if args.quiet:
        status = "PASS" if not failed else "FAIL"
        print(f"gate {status}: {len(results) - len(failed)}/{len(results)} gates passed")
        return 0 if not failed else 1

    print()
    print("=" * 72)
    print("Charlotte 311 Call Line -- quality gate")
    print("=" * 72)
    print()
    print(f"{'id':<5}{'gate':<34}{'status':<9}detail")
    print("-" * 72)
    for gate_id, label, passed, detail in results:
        status = "PASS" if passed else "FAIL"
        print(f"{gate_id:<5}{label:<34}{status:<9}{detail}")
    print("-" * 72)
    print()
    if failed:
        print(f"RESULT: FAIL ({len(failed)} of {len(results)} gates failed)")
        print()
        print("See QUALITY_BAR.md for the rationale behind each threshold.")
        print()
        return 1

    print("RESULT: PASS (all gates green)")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
