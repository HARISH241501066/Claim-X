"""Append-only audit log for reviewer decisions and pipeline runs.

It lives in its own SQLite file so it survives the pipeline regenerating the main database.
SQLite triggers reject any update or delete, so entries can only be added.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

AUDIT_PATH = Path(__file__).resolve().parent / "audit.db"

DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, event_type TEXT NOT NULL,
    case_id TEXT, action TEXT, reason TEXT, reviewer TEXT, details TEXT NOT NULL DEFAULT '{}');
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class AuditLog:
    def __init__(self, path: str | Path = AUDIT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(DDL)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _entry(row: sqlite3.Row) -> dict:
        entry = dict(row)
        entry["details"] = json.loads(entry["details"])
        return entry

    def append(
        self,
        event_type: str,
        *,
        case_id: str | None = None,
        action: str | None = None,
        reason: str | None = None,
        reviewer: str | None = None,
        details: dict | None = None,
    ) -> dict:
        con = self._connect()
        try:
            with con:
                cursor = con.execute(
                    "INSERT INTO audit_log (ts, event_type, case_id, action, reason, reviewer, details) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (now_iso(), event_type, case_id, action, reason, reviewer,
                     json.dumps(details or {}, sort_keys=True)),
                )  # fmt: skip
            row = con.execute(
                "SELECT * FROM audit_log WHERE audit_id = ?", (cursor.lastrowid,)
            ).fetchone()
        finally:
            con.close()
        return self._entry(row)

    def list(self, limit: int = 100, case_id: str | None = None) -> list[dict]:
        """Entries, latest first."""
        sql, params = "SELECT * FROM audit_log", ()
        if case_id is not None:
            sql, params = sql + " WHERE case_id = ?", (case_id,)
        con = self._connect()
        try:
            rows = con.execute(f"{sql} ORDER BY audit_id DESC LIMIT ?", (*params, limit)).fetchall()
        finally:
            con.close()
        return [self._entry(r) for r in rows]

    def latest_decisions(self) -> dict[str, dict]:
        """The most recent decision per case."""
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT * FROM audit_log WHERE event_type = 'decision' ORDER BY audit_id"
            ).fetchall()
        finally:
            con.close()
        return {r["case_id"]: self._entry(r) for r in rows}  # later rows overwrite earlier ones

    def latest_overrides(self) -> dict[str, dict]:
        """Active reviewer priority overrides per case; a later 'clear' removes the override."""
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT * FROM audit_log WHERE event_type = 'priority_override' ORDER BY audit_id"
            ).fetchall()
        finally:
            con.close()
        active: dict[str, dict] = {}
        for row in rows:
            entry = self._entry(row)
            if entry["action"] == "set_priority":
                active[entry["case_id"]] = entry
            else:
                active.pop(entry["case_id"], None)
        return active
