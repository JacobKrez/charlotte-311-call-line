# Evaluation

How to evaluate this system, what the metrics are, and which numbers
are measured versus targeted.

The distinction is load-bearing. A prototype that publishes a table of
percentages without saying how they were obtained is making a claim
about a system that has never run against real traffic.

## Summary

| Metric | Definition | Method | Target | Status |
|---|---|---|---|---|
| Emergency detection recall | Share of emergency transcripts that escalate | Red-team corpus | 100% | **Measured** on a self-authored corpus |
| Emergency detection false-positive rate | Share of non-emergency transcripts that escalate | Red-team corpus | ≤ 5% | **Measured** on a self-authored corpus |
| Injection detection recall | Share of injection transcripts that refuse | Red-team corpus | 100% | **Measured** on a self-authored corpus |
| Scope detection recall | Share of out-of-scope transcripts that redirect | Red-team corpus | 100% | **Measured** on a self-authored corpus |
| Confirmation integrity | Records written before the caller confirmed | Test suite | 0 | **Measured** |
| Slot extraction precision | Extracted values that match the labeled value | Test suite | 100% on the fixture set | **Measured** on synthetic fixtures |
| Slot extraction recall | Labeled values that were extracted | Test suite | ≥ 90% on the fixture set | **Measured** on synthetic fixtures |
| PII redaction recall | Identifier shapes removed | Test suite | 100% | **Measured** on synthetic fixtures |
| PII over-redaction | City-relevant values removed | Test suite | 0 | **Measured** on synthetic fixtures |
| Real-traffic escalation rate | Escalations per 1,000 real calls | Shadow deployment | Unknown | **Not measured.** No shadow deployment has been run |
| Real-traffic ASR error rate | Transcription error on real call audio | Shadow deployment | Unknown | **Not measured** |
| Deflection rate | Calls resolved without a human | Shadow deployment | Unknown | **Not measured.** This prototype has no deflection path |
| Caller satisfaction | Post-call survey | Not implemented | Unknown | **Not measured** |

Everything below the horizontal rule is measured. Everything above it
is a target or an open question.

---

## 1. Emergency detection recall

**Definition.** The share of emergency transcripts in the corpus that
produce an escalation.

**Method.** `redteam/run_redteam.py` over
`redteam/attack_cases.jsonl`. The harness is deterministic and offline,
and makes no model calls.

**Target.** 100%.

**Why 100% and not 95%.** The cost of a miss is asymmetric and not
tradeable. A missed emergency means a resident reports an active
intrusion to a non-emergency line and is told the report has been
filed. Nothing is coming. A false positive costs a human handoff on a
non-emergency call, which is expensive and annoying but survivable.
There is no value of this threshold at which 5% misses are acceptable,
so the threshold is 100% and the margin is taken on the false-positive
side instead.

**Current status.** 12 of 12 emergency cases pass.

**What this number does not mean.** The corpus was written by the
author of the detector. It measures internal consistency between the
corpus and the detector, not generalization to calls nobody
anticipated. See "The corpus problem" below.

## 2. Emergency detection false-positive rate

**Definition.** The share of non-emergency transcripts that produce an
escalation.

**Method.** The `false_positive` category in the same corpus.

**Target.** At most 5%.

**Why not 0%.** Over-escalation is not free. Every false positive
costs a human agent's time on a call that did not need one, and a
caller who is escalated for a routine noise report learns that this
line does not listen. The threshold is 5% because the detector matches
phrases rather than meaning, so a paraphrase gap is expected rather
than exceptional.

**Why not 100% coverage.** A threshold of 0% false positives would mean
the detector only fires on one exact phrasing of an emergency, which
would be useless. The false-positive set is where the cost of the
emergency threshold is paid.

**Current status.** 13 of 13 pass.

## 3. Injection detection recall

**Definition.** The share of injection transcripts that produce a
refusal rather than proceeding.

**Method.** The `injection` category in the corpus.

**Target.** 100%.

**Why 100%.** A missed injection lets a caller write an unconfirmed
record onto the city's system. The confirmation steps exist for
exactly this, so bypassing them defeats the property the confirmations
were there to provide.

**Current status.** 18 of 18 pass.

## 4. Scope detection recall

**Definition.** The share of out-of-scope transcripts that produce a
redirect rather than an answer.

**Method.** The `scope` category in the corpus.

**Target.** 100%.

**Why 100%.** Out-of-scope handling is a redirect, and missing one
means the line answers a question it should not answer, or mishandles
a payment, legal, or immigration request that it has no business
touching.

**Current status.** 7 of 7 pass.

## 5. Confirmation integrity

**Definition.** The number of case records written before the caller
confirmed the report.

**Method.** `tests/test_dialog.py::TestReportFiling`. The test drives
a report to the final details prompt, asserts the store is empty at
every intermediate step, then confirms and asserts exactly one record
exists with the expected field values.

**Target.** 0.

**Why this is a metric and not just a test.** This is the property
that separates a prototype from something that could be trusted to
write to a municipal system. It is the one property where the failure
is a false record rather than a bad interaction.

**Current status.** 0.

## 6. Slot extraction precision and recall

**Definition.** Precision: extracted values that match the labeled
value. Recall: labeled values that were extracted.

**Method.** `tests/test_slots.py`, against a hand-labeled fixture set.

**Target.** 100% precision, at least 90% recall.

**Why the asymmetry.** A false positive here is a wrong value on a
municipal record. A false negative is the agent asking again, which
costs the caller a few seconds. The extractor is deliberately
conservative, and it declines to produce a value rather than guessing
one: "hello", "towed", and "please" must not become license plates,
and "down the street" must not become an address.

**Current status.** 100% precision and 100% recall on the current
fixture set of 9 plates, 4 addresses, 6 times, and 3 detail strings.

**What this does not measure.** The fixture set is synthetic and
written by the same author as the extractor. It is a regression suite,
not a benchmark. There is no held-out set.

## 7. PII redaction recall and over-redaction

**Definition.** Recall: identifier shapes removed. Over-redaction:
city-relevant values removed.

**Method.** `tests/test_pii.py`.

**Target.** 100% recall, 0 over-redaction.

**Why 0 over-redaction.** Over-redaction that blanks a location
produces a useless case record. That is a worse failure than a phone
number surviving into an access-controlled log, because the record is
the thing the city acts on.

**Current status.** 100% on both. The test suite asserts that an
address, a plate, and a time all survive redaction intact.

## 8. Real-traffic escalation rate

**Definition.** Escalations per 1,000 real inbound calls.

**Method.** A shadow deployment: the agent processes real call audio
alongside the existing line, without the caller knowing, and every
escalation and non-escalation is labeled by a human reviewer.

**Target.** Unknown. This number cannot be set before the first
shadow run, because it depends on the real traffic distribution, and
guessing it in advance would be a way of avoiding finding out.

**Status.** Not measured. No shadow deployment has been run. This is
the highest-value missing measurement in the project.

**Why it matters.** Every threshold above is measured against a corpus
written by the author. This is the number that would tell us whether
the author guessed the distribution correctly.

## 9. Real-traffic ASR error rate

**Definition.** Word error rate of the speech-to-text component on
real call audio, and separately, the rate at which a transcription
error changes the agent's classification.

**Method.** The shadow deployment, with transcripts compared against
hand-written ground truth for a sample.

**Target.** Unknown.

**Status.** Not measured.

**Why the second number is the interesting one.** A word error rate on
its own says little. What matters is whether an error flips a
decision. A caller saying "my car was towed" transcribed as "my car
was loud" is an error and a misclassification, and the rate of *those*
is the number that determines whether the emergency detector needs to
be more conservative than the corpus suggests.

## 10. Deflection rate

**Definition.** The share of calls resolved end to end without a human
agent.

**Target.** Not applicable to this prototype. This system has no
deflection path: it either resolves a lookup or files a report, and
neither involves a human being skipped, because this prototype is not
connected to a line with humans to skip.

**Status.** Not measured, and not currently meaningful.

**Why it is in this document anyway.** Deflection rate is the metric
that determines whether a 311 automation is worth deploying. Charlotte
handles roughly 925,000 calls a year at 75 to 80 agents, so even a
modest deflection rate is a large operational effect. Any evaluation
that omits it is evaluating the wrong thing.

## 11. Caller satisfaction

**Definition.** Post-call survey, or a sampled qualitative review of
call recordings.

**Status.** Not implemented.

**Why it is the metric most likely to be missed.** Every metric above
is a system-side measurement. The caller-side question, "did this line
help?", is the one that determines whether anyone uses the line, and
it is the one that cannot be inferred from the others. A system that
escalates every call to a human is safe and useless.

---

## The corpus problem

The red-team corpus in `redteam/attack_cases.jsonl` was written by the
same author as the detector it tests. This section exists because a
100% pass rate on such a corpus is easy to misread.

**What a 100% pass rate on this corpus does mean.** The corpus and the
detector agree on the cases in the corpus. The detector's behavior is
documented, reproducible, and checked into version control. The
ordering argument holds on the overlap cases. The benign-context and
past-tense exemptions do not over-fire on the cases written for them.

**What it does not mean.** It says nothing about calls that nobody
thought to write down. The corpus contains "someone is breaking into
my house right now" and it does not contain "he is currently in my
residence" or "there is a person inside my place". Those would very
likely be missed, because the detector matches phrases.

**Why the corpus records a "why it matters" field.** Every case in the
corpus carries a one-line reason. A corpus of transcripts alone would
be a list of inputs and expected outputs; the reason field is what
makes it a set of *failure modes* rather than a set of examples. When
someone adds a case, the reason is the part that tells a future
maintainer what class of failure it protects against and whether it
can be relaxed.

**How the gap gets closed.** In priority order:

1. A corpus written by people who did not build the detector.
2. Real call audio, shadow-processed, with human labeling.
3. Paraphrase generation by a language model, filtered into a corpus
   by a human reviewer. This is the interesting one, because it
   directly targets the phrase-matching weakness. It is also a
   self-authored corpus in a subtler way, since the generator and the
   detector share an author, so it needs to be labeled by someone else
   and the labels need to be checked.

## Reproducing these numbers

```bash
python3 -m pytest -q                    # metrics 5, 6, 7
python3 redteam/run_redteam.py          # metrics 1, 2, 3, 4
python3 scripts/gate.py                 # everything, as a gate
```

All three are deterministic and offline. None of them requires an API
key, and none of them makes a network call.
