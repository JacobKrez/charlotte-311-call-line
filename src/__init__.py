"""Charlotte 311 non-emergency call line.

A voice agent prototype for a municipal 311 line, built around the
failure modes that matter when a public-facing phone line handles
emergency-adjacent requests.

The package is organized so the safety-critical parts are importable
and testable on their own:

``src.guardrails``
    The guardrail chain. See its module docstring for the ordering
    rationale.
``src.dialog``
    The explicit dialogue state machine.
``src.slots``
    Rule-based slot extraction.
``src.store``
    Case records and the audit log.
``src.audio``
    The transcript-source and synthesizer protocols, plus offline and
    provider implementations.
``src.cli``
    The runner.
"""

__version__ = "0.1.0"
