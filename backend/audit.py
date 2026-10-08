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
    input_tokens INTEGER, output_tokens INTEGER, detail TEXT,
    attempt INTEGER NOT NULL DEFAULT 1);
CREATE TRIGGER IF NOT EXISTS llm_requests_no_update BEFORE UPDATE ON llm_requests
BEGIN SELECT RAISE(ABORT, 'llm_requests is append-only'); END;
CREATE TRIGGER IF NOT EXISTS llm_requests_no_delete BEFORE DELETE ON llm_requests
BEGIN SELECT RAISE(ABORT, 'llm_requests is append-only'); END;

-- Accepted LLM briefs, kept in their masked form (placeholders such as PERSON_1) so no real value
-- is stored here. The same evidence rebuilds the same placeholders, which restores the names.
CREATE TABLE IF NOT EXISTS brief_cache (
    case_id TEXT NOT NULL, horizon INTEGER NOT NULL, provider TEXT NOT NULL,
    pack_hash TEXT NOT NULL, masked_text TEXT NOT NULL, model TEXT,
    input_tokens INTEGER, output_tokens INTEGER, created_at TEXT NOT NULL,
    PRIMARY KEY (case_id, horizon, provider, pack_hash));
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
            columns = {row[1] for row in con.execute("PRAGMA table_info(llm_requests)")}
            if "attempt" not in columns:  # an older file: add the column, keep every row
                con.execute("ALTER TABLE llm_requests ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1")

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

    def list(
        self, limit: int = 100, case_id: str | None = None, event_type: str | None = None
    ) -> list[dict]:
        """Entries, latest first."""
        clauses, params = [], ()
        if case_id is not None:
            clauses.append("case_id = ?")
            params += (case_id,)
        if event_type is not None:
            clauses.append("event_type = ?")
            params += (event_type,)
        sql = "SELECT * FROM audit_log" + (" WHERE " + " AND ".join(clauses) if clauses else "")
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
        attempt: int = 1,
    ) -> dict:
        """Log one LLM attempt. `audit` is masker.audit_record(): token kinds and dropped field
        names only. `detail` is a short reason code, never reply text."""
        con = self._connect()
        try:
            with con:
                cursor = con.execute(
                    "INSERT INTO llm_requests (ts, case_id, provider, model, outcome, tokens_by_kind, "
                    "dropped_fields, input_tokens, output_tokens, detail, attempt) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (now_iso(), case_id, provider, model, outcome,
                     json.dumps(audit.get("tokens_by_kind", {}), sort_keys=True),
                     json.dumps(audit.get("dropped_fields", [])), input_tokens, output_tokens,
                     (detail or None) and detail[:DETAIL_LIMIT], attempt),
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

    def get_cached_brief(
        self, case_id: str, horizon: int, provider: str, pack_hash: str
    ) -> dict | None:
        """The accepted masked brief for exactly this evidence, or None."""
        con = self._connect()
        try:
            row = con.execute(
                "SELECT * FROM brief_cache WHERE case_id = ? AND horizon = ? AND provider = ? "
                "AND pack_hash = ?",
                (case_id, horizon, provider, pack_hash),
            ).fetchone()
        finally:
            con.close()
        return dict(row) if row else None

    def put_cached_brief(
        self,
        *,
        case_id: str,
        horizon: int,
        provider: str,
        pack_hash: str,
        masked_text: str,
        model: str | None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """Store an accepted brief and drop older ones for the same case, horizon and provider."""
        con = self._connect()
        try:
            with con:
                con.execute(
                    "DELETE FROM brief_cache WHERE case_id = ? AND horizon = ? AND provider = ? "
                    "AND pack_hash <> ?",
                    (case_id, horizon, provider, pack_hash),
                )
                con.execute(
                    "INSERT OR REPLACE INTO brief_cache (case_id, horizon, provider, pack_hash, "
                    "masked_text, model, input_tokens, output_tokens, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (case_id, horizon, provider, pack_hash, masked_text, model, input_tokens,
                     output_tokens, now_iso()),
                )  # fmt: skip
        finally:
            con.close()

    def list_cached_briefs(self) -> list[dict]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT * FROM brief_cache ORDER BY case_id, horizon, provider"
            ).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]

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
