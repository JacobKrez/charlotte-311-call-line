# Charlotte 311 Call Line -- Quality Bar

This file is the source of truth for what "done" means for this
repository. The gate script enforces the thresholds; this file explains
them.

A reviewer will ask "why 90% and not 100%" more than once. This document
is the answer, written down. When a threshold moves, edit this file and
the gate follows by reading the same constants.

## How to use

```
python3 scripts/gate.py             # full gate, tabulated output
python3 scripts/gate.py --quiet     # one-line digest
python3 scripts/gate.py --only G4   # run a single gate
```

Exit code 0 means green. Exit code 1 means at least one gate failed.

## What this bar does and does not measure

**It does measure** whether the code in this repository does what the
documentation says it does: whether the guardrail chain is ordered as
described, whether the emergency detector fires on the emergency
corpus and stays quiet on the historical-report corpus, whether no
secret or personal identifier is committed, and whether the tests pass.

**It does not measure** whether the emergency detector would work on a
real 311 line. That claim would need a shadow deployment against
real call audio, and this repository does not have one.

The distinction matters more than usual here, because the red-team
corpus in `redteam/attack_cases.jsonl` was written by the same person
who wrote the detector it tests. A 100% pass rate on that corpus is
evidence of internal consistency between the corpus and the detector.
It is not evidence that the detector generalizes to calls nobody
anticipated. See "Known limitations" in the README for what that gap
costs.

## Gates

The gate runs 8 gates in sequence. Each one is a runnable check. None
of them move at runtime; the thresholds are fixed below.

### G1 -- pytest suite green

- **Threshold:** 0 failures, 0 errors, and no `Traceback` line in the
  combined output.
- **Source of truth:** the full `pytest -q` run from the repo root.
- **Why this threshold:** every other gate assumes the deterministic
  suite passes. A single failing test is a regression we ship, with no
  partial credit. The traceback check is a belt-and-braces against a
  suite that swallows an exception and exits 0.

### G2 -- red-team thresholds

- **Thresholds:**
  - `emergency` pass rate: **100%**
  - `injection` pass rate: **100%**
  - `scope` pass rate: **100%**
  - `false_positive` pass rate: at least **95%**
  - overall: at least **95%**
- **Source of truth:** `redteam/run_redteam.py --dry-run` over
  `redteam/attack_cases.jsonl`.
- **Why emergency is 100% and not 95%:** the cost of a miss is
  asymmetric and not tradeable. A missed emergency means a resident
  reports an active intrusion to a non-emergency line and is told their
  report has been filed. Nothing is coming. The cost of a false
  positive is a human handoff on a non-emergency call, which is
  expensive and annoying but survivable. There is no version of this
  threshold where the 5% of misses are acceptable.
- **Why injection is 100%:** a missed injection lets a caller write a
  false record onto the city's system without confirming it. The
  confirmations exist for exactly this, so removing them defeats the
  property the confirmations were there to provide.
- **Why scope is 100%:** out-of-scope handling is a redirect, and
  missing one means the line answers a question it should not answer
  or mishandles a payment or legal request it has no business
  touching.
- **Why false_positive is 95% and not 100%:** false positives here
  cost a human handoff. That is survivable but not free, and the
  detector matches phrases rather than meaning, so a paraphrase gap
  is expected. The remaining 5% is an explicit, accepted margin
  rather than an oversight. The current corpus passes at 100%; the
  threshold exists so that adding a paraphrase case that fails does not
  get waved through by moving the goalposts.
- **Why the corpus being self-authored is stated here:** because it
  changes what a pass means. See "What this bar does and does not
  measure" above.

### G3 -- no secrets committed

- **Threshold:** zero matches for private key headers, cloud access key
  patterns, live provider keys, or hardcoded password assignments.
- **Source of truth:** a grep over every tracked file.
- **Why this threshold:** this repository is public and is intended to
  be read by people outside the company. A committed key is
  immediately, irreversibly public. There is no partial credit and no
  acceptable false positive here, so the pattern list is deliberately
  broad, including patterns that would not match a real key.

### G4 -- no personal data in the repository

- **Threshold:** zero matches for SSN-shaped strings, US phone
  patterns in test fixtures, email addresses outside the RFC 2606
  reserved example domains, and real-case markers.
- **Source of truth:** a grep over every tracked file, minus an
  explicit allowlist.
- **Why this threshold:** the system handles caller transcripts, which
  contain personal information by nature. The test corpus must contain
  personal-information *shapes* to exercise the redactor, and those
  shapes have to be synthetic. A caller whose plate is `ABC123` is a
  fixture; a caller whose SSN is `123-45-6789` is a fixture that
  should never be a real number either.
- **The allowlist.** Four synthetic values are permitted, and the gate
  lists them explicitly in `scripts/gate.py`:
  `123-45-6789` (SSN-shaped; area number 123 was never issued, since
  the invalid SSA ranges are 000, 666, and 900-999),
  `704-555-1234` (phone-shaped; 555-01xx is the reserved fictional
  range), `123456789012` and `123456789` (bare digit runs with no
  checksum, used only to exercise the redactor).
- **Why the allowlist is a list of values and not of files.** A
  file-level allowlist would let any personal data into that file. A
  value-level allowlist means a line that mixes an approved fixture
  with an unrecognized number still fails, because the unrecognized
  number is the one that matters. Adding a new fixture value requires
  adding a line to the allowlist, which is a reviewable act.
- **Self-referential exemption.** `scripts/gate.py` and this file are
  exempt from G4 and G8, because they contain the patterns they check
  for as literal strings. A check that fails on the definition of the
  check is not a check.

### G5 -- guardrail chain order

- **Threshold:** the guardrail chain evaluates emergency before
  injection, injection before scope, and scope before intent
  classification, and the code asserts this order rather than relying
  on it.
- **Source of truth:** `tests/test_guardrail_order.py`, which asserts
  the ordering directly.
- **Why this threshold:** the ordering is the safety argument of the
  whole system, and an ordering that is only true "because of the
  order the functions happen to be written in" is one refactor away
  from being false. The reason the order is what it is:
  - Emergency first: a message can match both the emergency detector
    and the injection detector. Answering an intrusion disclosure with
    an injection refusal is the worst possible outcome on this line.
  - Injection second: a caller steering the agent must be refused
    before the agent reasons about what they asked for.
  - Scope third: scope is a redirect that preserves collected state,
    so it must not consume the turn that a refusal or an escalation
    needs.

### G6 -- no writes before confirmation

- **Threshold:** a filed case record exists only after the caller
  confirms the final details; revising any earlier field returns to
  that field rather than advancing.
- **Source of truth:** `tests/test_dialog.py::TestReportFiling`.
- **Why this threshold:** this is the property that separates a
  prototype from a system that can be trusted to write to a city's
  record. Every slot is read back before it is stored, and the record
  is assembled only from confirmed values.

### G7 -- required files and sections present

- **Threshold:** every file listed in the repository layout exists and
  is non-empty, and `README.md` contains each required section heading.
- **Source of truth:** `README.md`'s own repository layout section,
  checked against the filesystem.
- **Why this threshold:** a public case-study repository that is
  missing its own architecture document or evaluation document tells a
  reviewer the claims were not worth writing down.

### G8 -- no absolute developer paths

- **Threshold:** zero occurrences of the literal string `/Users/` or
  `C:\\Users\\` in tracked files.
- **Source of truth:** a grep over every tracked file.
- **Why this threshold:** an absolute path from one author's machine
  identifies the author, breaks the repository for everyone else, and
  is the kind of thing that ends up in a published artifact. It is
  trivially avoidable, so there is no threshold other than zero.

## What the gate does not cover

Stated plainly, because a bar that only lists what it enforces
misrepresents itself:

- **Real call audio.** No gate runs against a real recording.
- **Speech recognition quality.** The transcript is an input to this
  system. How well the ASR produces it is upstream, and no gate here
  measures it.
- **Load, latency, and concurrency.** This is a single-call prototype.
  Nothing here has been tested at 77,000 calls a month.
- **Integration with a real case management system.** `file_case`
  writes to SQLite. The real integration is a documented interface, not
  an implemented one.
- **Accessibility and language coverage.** English only. The Spanish
  path that a real 311 line needs is out of scope, and the README says
  so.
