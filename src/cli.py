"""Text-mode call runner.

This is the entry point for both the demo and the tests. It drives the
dialogue state machine from a transcript source, optionally rendering
audio, and writes case records and the audit log.

Running it with the offline components needs no API key and no network:

    python3 -m src.cli --script "one, abc 123, yes" --record demo.db

The same code path runs against a real provider by swapping the
transcript source and the synthesizer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audio import ScriptedTranscriptSource, SilentSynthesizer
from .dialog import Dialog, State
from .store import Store


def build_dialog(store: Store) -> Dialog:
    """Build a dialogue wired to a store for case records and audit."""
    return Dialog(
        on_file=store.file_case,
        on_escalate=lambda reason: store.append_audit(
            state="escalated",
            disposition="escalate",
            transcript="",
            notes=(f"escalation:{reason}",),
        ),
        on_audit=lambda **kwargs: store.append_audit(**kwargs),
    )


def run_scripted(
    transcripts: list[str],
    store: Store,
    *,
    synthesize: bool = False,
    audio_dir: Path | None = None,
) -> list[dict]:
    """Run a call from a scripted list of transcripts.

    Returns one dict per turn, for the caller to inspect or assert on.
    """
    source = ScriptedTranscriptSource(transcripts)
    dialog = build_dialog(store)
    synthesizer = SilentSynthesizer()

    turns: list[dict] = []
    turn_index = 0

    while True:
        transcript = source.next_transcript()
        if transcript is None:
            break

        turn = dialog.process(transcript)
        turns.append(
            {
                "input": transcript,
                "state": turn.state.value,
                "response": turn.response,
                "terminal": turn.terminal,
                "escalated": turn.escalated,
                "notes": list(turn.notes),
                "record_filed": turn.record_filed,
            }
        )

        # The audit row is written by Dialog's own on_audit callback,
        # so that every caller of process() gets an audit trail. Do not
        # log again here.
        if synthesize and audio_dir is not None:
            audio_dir.mkdir(parents=True, exist_ok=True)
            synthesizer.synthesize(
                turn.response, audio_dir / f"turn_{turn_index:03d}.wav"
            )
        turn_index += 1

        if turn.terminal:
            break

    return turns


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m src.cli",
        description="Run a simulated 311 non-emergency call.",
    )
    parser.add_argument(
        "--script",
        required=True,
        help="Comma-separated caller transcripts. The call ends when the list runs out.",
    )
    parser.add_argument(
        "--record",
        default=":memory:",
        help="SQLite file for case records and the audit log. Default: in-memory.",
    )
    parser.add_argument(
        "--audio-dir",
        default=None,
        help="Directory to write rendered agent audio into.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the turn log as JSON instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    transcripts = [t.strip() for t in args.script.split(",") if t.strip()]
    store = Store(args.record)

    try:
        turns = run_scripted(
            transcripts,
            store,
            synthesize=bool(args.audio_dir),
            audio_dir=Path(args.audio_dir) if args.audio_dir else None,
        )
    finally:
        if args.record != ":memory:":
            store.close()

    if args.json:
        print(json.dumps(turns, indent=2, default=str))
        return 0

    for turn in turns:
        print(f"caller: {turn['input']}")
        print(f"agent : {turn['response']}")
        if turn["notes"]:
            print(f"notes : {', '.join(turn['notes'])}")
        if turn["record_filed"]:
            print(f"filed : {turn['record_filed']['report_id']}")
        print("-" * 60)

    print(f"turns: {len(turns)}")
    if args.record != ":memory:":
        print(f"cases filed: {store.count_cases()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
