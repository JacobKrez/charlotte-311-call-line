"""Record storage and the audit log.

Two different things get written, and they have different rules.

**Case records** are the product. A filed report is a record on the
city's system, and it needs to be queryable by case type, location, and
time. This prototype stores them in SQLite, which is the same substrate
the case-management layer would use in production.

**The audit log** is the evidence. Every turn is appended with the
state it happened in, the guardrail disposition, and what was redacted.
It exists so that a question like "why did this caller get escalated"
has an answer that was written down at the time, rather than
reconstructed later from memory.

Both write through this module so that the redaction rule cannot be
bypassed by a caller path that forgot to apply it. There is exactly one
place where a transcript becomes a stored row.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    report_id   TEXT PRIMARY KEY,
    incident_type TEXT NOT NULL,
    location    TEXT,
    time_of_incident TEXT,
    details     TEXT,
    status      TEXT NOT NULL DEFAULT 'Filed',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id   TEXT,
    state       TEXT NOT NULL,
    disposition TEXT NOT NULL,
    transcript  TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cases_type ON cases (incident_type);
CREATE INDEX IF NOT EXISTS idx_cases_location ON cases (location);
CREATE INDEX IF NOT EXISTS idx_audit_report ON audit_log (report_id);
"""


@dataclass
class Store:
    """SQLite-backed store for case records and the audit log.

    Parameters
    ----------
    path:
        Path to the SQLite file. Use ``":memory:"`` for tests.
    """

    path: str = ":memory:"

    def __post_init__(self) -> None:
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # -- writes ----------------------------------------------------------

    def file_case(self, record: dict) -> dict:
        """Write a case record and return it with its identifier.

        The details field is redacted before it is stored. The location
        and time are not: they are the substance of the record, and
        redacting them would produce a useless case.
        """
        from .guardrails import prepare_for_log

        report_id = str(uuid.uuid4())
        details = record.get("details") or ""
        redacted_details, _ = prepare_for_log(details)

        self._conn.execute(
            """
            INSERT INTO cases (report_id, incident_type, location, time_of_incident, details)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                report_id,
                record.get("incident_type", "unknown"),
                record.get("location"),
                record.get("time"),
                redacted_details,
            ),
        )
        self._conn.commit()
        return {"report_id": report_id, "status": "Filed", **record}

    def append_audit(
        self,
        *,
        state: str,
        disposition: str,
        transcript: str,
        notes: tuple[str, ...] = (),
        report_id: str | None = None,
    ) -> None:
        """Append one turn to the audit log.

        The transcript is redacted here, at the single point where a
        transcript becomes a stored row. Callers pass the raw text and
        rely on this to redact it, so that a new code path cannot
        accidentally store an unredacted transcript.
        """
        from .guardrails import prepare_for_log

        redacted, pii_notes = prepare_for_log(transcript)
        all_notes = list(notes) + list(pii_notes)
        self._conn.execute(
            """
            INSERT INTO audit_log (report_id, state, disposition, transcript, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                report_id,
                state,
                disposition,
                redacted,
                json.dumps(all_notes),
            ),
        )
        self._conn.commit()

    # -- reads -----------------------------------------------------------

    def get_case(self, report_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM cases WHERE report_id = ?", (report_id,)
        ).fetchone()
        return dict(row) if row else None

    def count_cases(self, incident_type: str | None = None) -> int:
        if incident_type is None:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM cases").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM cases WHERE incident_type = ?",
                (incident_type,),
            ).fetchone()
        return int(row["n"])

    def audit_rows(self, report_id: str | None = None) -> list[dict]:
        if report_id is None:
            rows = self._conn.execute(
                "SELECT * FROM audit_log ORDER BY id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM audit_log WHERE report_id = ? ORDER BY id",
                (report_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._conn.close()
