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

-- One row per LLM request attempt: which kinds of values were masked and how many tokens were
-- used. It never holds real values, the vault, the prompt or the reply.
CREATE TABLE IF NOT EXISTS llm_requests (
    request_id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, case_id TEXT NOT NULL,
    provider TEXT NOT NULL, model TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('llm_ok', 'validation_failed', 'leak_blocked', 'error')),
    tokens_by_kind TEXT NOT NULL, dropped_fields TEXT NOT NULL,
    input_tokens INTEGER, output_tokens INTEGER, detail TEXT);
CREATE TRIGGER IF NOT EXISTS llm_requests_no_update BEFORE UPDATE ON llm_requests
BEGIN SELECT RAISE(ABORT, 'llm_requests is append-only'); END;
CREATE TRIGGER IF NOT EXISTS llm_requests_no_delete BEFORE DELETE ON llm_requests
BEGIN SELECT RAISE(ABORT, 'llm_requests is append-only'); END;
"""

DETAIL_LIMIT = 300


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

    def record_llm_request(
        self,
        *,
        case_id: str,
        provider: str,
        model: str | None,
        outcome: str,
        audit: dict,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        detail: str | None = None,
    ) -> dict:
        """Log one LLM attempt. `audit` is masker.audit_record(): token kinds and dropped field
        names only. `detail` is a short reason code, never reply text."""
        con = self._connect()
        try:
            with con:
                cursor = con.execute(
                    "INSERT INTO llm_requests (ts, case_id, provider, model, outcome, tokens_by_kind, "
                    "dropped_fields, input_tokens, output_tokens, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (now_iso(), case_id, provider, model, outcome,
                     json.dumps(audit.get("tokens_by_kind", {}), sort_keys=True),
                     json.dumps(audit.get("dropped_fields", [])), input_tokens, output_tokens,
                     (detail or None) and detail[:DETAIL_LIMIT]),
                )  # fmt: skip
            row = con.execute(
                "SELECT * FROM llm_requests WHERE request_id = ?", (cursor.lastrowid,)
            ).fetchone()
        finally:
            con.close()
        return self._llm_entry(row)

    @staticmethod
    def _llm_entry(row: sqlite3.Row) -> dict:
        entry = dict(row)
        entry["tokens_by_kind"] = json.loads(entry["tokens_by_kind"])
        entry["dropped_fields"] = json.loads(entry["dropped_fields"])
        return entry

    def list_llm_requests(self, limit: int = 100) -> list[dict]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT * FROM llm_requests ORDER BY request_id DESC LIMIT ?", (limit,)
            ).fetchall()
        finally:
            con.close()
        return [self._llm_entry(r) for r in rows]

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
