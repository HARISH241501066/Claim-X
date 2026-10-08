"""Storage for notifications, the cases already seen, and drafted outbound messages.

These tables live in the audit database file (next to the append-only log) so they survive the
pipeline regenerating the main database. Unlike the audit log they may be updated: a notification
is marked read, a draft is edited. Every change is also written to the append-only audit log by
the caller.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from backend.audit import now_iso

ROLES = ("siu", "manager")
SEVERITIES = ("info", "warning", "high")
EMAIL_STATUSES = ("not_required", "sent", "failed", "skipped_cooldown", "disabled")
RECIPIENT_TYPES = ("provider", "member")
OUTBOUND_STATUSES = ("draft", "approved", "sent_simulated")

DDL = f"""
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_role TEXT NOT NULL CHECK (recipient_role IN {ROLES}),
    type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN {SEVERITIES}),
    case_id TEXT, message TEXT NOT NULL, created_at TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0,
    email_status TEXT NOT NULL DEFAULT 'not_required' CHECK (email_status IN {EMAIL_STATUSES}),
    dedupe_key TEXT,
    recipient_user_id INTEGER);  -- set for a message meant for one person (an assignment)
CREATE INDEX IF NOT EXISTS notifications_dedupe ON notifications (dedupe_key);

-- What each run has already told reviewers about, so a rerun does not repeat itself.
CREATE TABLE IF NOT EXISTS case_seen (
    case_id TEXT PRIMARY KEY, first_seen TEXT NOT NULL, finding_ids TEXT NOT NULL,
    last_email_at TEXT);

CREATE TABLE IF NOT EXISTS outbound_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL,
    recipient_type TEXT NOT NULL CHECK (recipient_type IN {RECIPIENT_TYPES}),
    recipient_id TEXT NOT NULL, template TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN {OUTBOUND_STATUSES}),
    created_by TEXT NOT NULL, approved_by TEXT, created_at TEXT NOT NULL, sent_at TEXT);
"""

SEVERITY_RANK = {"high": 0, "warning": 1, "info": 2}


class NotifyStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as con, con:
            con.executescript(DDL)
            columns = {row[1] for row in con.execute("PRAGMA table_info(notifications)")}
            if "recipient_user_id" not in columns:  # a file made before per-person messages
                con.execute("ALTER TABLE notifications ADD COLUMN recipient_user_id INTEGER")

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        con = self._connect()
        try:
            row = con.execute(sql, params).fetchone()
        finally:
            con.close()
        return dict(row) if row else None

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        con = self._connect()
        try:
            return [dict(r) for r in con.execute(sql, params).fetchall()]
        finally:
            con.close()

    def _write(self, sql: str, params: tuple = ()) -> int:
        con = self._connect()
        try:
            with con:
                return con.execute(sql, params).lastrowid or 0
        finally:
            con.close()

    # ------------------------------------------------------------ notifications

    def add_notification(
        self, *, role: str, type: str, severity: str, case_id: str | None, message: str,
        email_status: str = "not_required", dedupe_key: str | None = None,
        recipient_user_id: int | None = None,
    ) -> dict:  # fmt: skip
        new_id = self._write(
            "INSERT INTO notifications (recipient_role, type, severity, case_id, message, "
            "created_at, email_status, dedupe_key, recipient_user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (role, type, severity, case_id, message, now_iso(), email_status, dedupe_key,
             recipient_user_id),
        )  # fmt: skip
        return self.get_notification(new_id)

    def get_notification(self, notification_id: int) -> dict | None:
        row = self._one("SELECT * FROM notifications WHERE id = ?", (notification_id,))
        return self._notification(row) if row else None

    def has_dedupe_key(self, key: str) -> bool:
        return self._one("SELECT 1 AS x FROM notifications WHERE dedupe_key = ?", (key,)) is not None

    def latest_with_key_prefix(self, prefix: str, case_id: str) -> dict | None:
        row = self._one(
            "SELECT * FROM notifications WHERE case_id = ? AND type = ? ORDER BY id DESC LIMIT 1",
            (case_id, prefix),
        )
        return self._notification(row) if row else None

    def set_email_status(self, notification_id: int, status: str) -> None:
        self._write("UPDATE notifications SET email_status = ? WHERE id = ?", (status, notification_id))

    def list_notifications(
        self, role: str | None = None, unread_only: bool = False, limit: int = 200
    ) -> list[dict]:  # fmt: skip
        sql, params = "SELECT * FROM notifications WHERE 1 = 1", []
        if role:
            sql, params = sql + " AND recipient_role = ?", [*params, role]
        if unread_only:
            sql += " AND read = 0"
        rows = [self._notification(r) for r in self._all(sql + " ORDER BY id DESC", tuple(params))]
        rows.sort(key=lambda n: SEVERITY_RANK[n["severity"]])  # stable: newest first within a level
        return rows[:limit]

    def unread_count(self, role: str | None = None) -> int:
        sql, params = "SELECT COUNT(*) AS n FROM notifications WHERE read = 0", ()
        if role:
            sql, params = sql + " AND recipient_role = ?", (role,)
        return self._one(sql, params)["n"]

    def mark_read(self, notification_id: int) -> bool:
        con = self._connect()
        try:
            with con:
                return con.execute(
                    "UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,)
                ).rowcount > 0
        finally:
            con.close()

    def mark_all_read(self, role: str | None = None) -> int:
        con = self._connect()
        try:
            with con:
                if role:
                    cursor = con.execute(
                        "UPDATE notifications SET read = 1 WHERE read = 0 AND recipient_role = ?", (role,)
                    )
                else:
                    cursor = con.execute("UPDATE notifications SET read = 1 WHERE read = 0")
                return cursor.rowcount
        finally:
            con.close()

    @staticmethod
    def _notification(row: dict) -> dict:
        out = dict(row)
        out["read"] = bool(out["read"])
        out.pop("dedupe_key", None)
        return out

    # ------------------------------------------------------------ cases already seen

    def get_seen(self, case_id: str) -> dict | None:
        row = self._one("SELECT * FROM case_seen WHERE case_id = ?", (case_id,))
        if row:
            row["finding_ids"] = set(json.loads(row["finding_ids"]))
        return row

    def put_seen(self, case_id: str, finding_ids: list[str], first_seen: str | None = None) -> None:
        existing = self._one("SELECT first_seen FROM case_seen WHERE case_id = ?", (case_id,))
        self._write(
            "INSERT INTO case_seen (case_id, first_seen, finding_ids) VALUES (?, ?, ?) "
            "ON CONFLICT(case_id) DO UPDATE SET finding_ids = excluded.finding_ids",
            (case_id, (existing or {}).get("first_seen") or first_seen or now_iso(),
             json.dumps(sorted(finding_ids))),
        )  # fmt: skip

    def set_last_email(self, case_id: str, when: str) -> None:
        self._write("UPDATE case_seen SET last_email_at = ? WHERE case_id = ?", (when, case_id))

    # ------------------------------------------------------------ outbound drafts

    def add_outbound(
        self, *, case_id: str, recipient_type: str, recipient_id: str, template: str,
        subject: str, body: str, created_by: str,
    ) -> dict:  # fmt: skip
        new_id = self._write(
            "INSERT INTO outbound_messages (case_id, recipient_type, recipient_id, template, subject, "
            "body, status, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?)",
            (case_id, recipient_type, recipient_id, template, subject, body, created_by, now_iso()),
        )
        return self.get_outbound(new_id)

    def get_outbound(self, outbound_id: int) -> dict | None:
        return self._one("SELECT * FROM outbound_messages WHERE id = ?", (outbound_id,))

    def list_outbound(self, case_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM outbound_messages WHERE case_id = ? ORDER BY id DESC", (case_id,)
        )

    def update_outbound(self, outbound_id: int, subject: str, body: str) -> None:
        self._write(
            "UPDATE outbound_messages SET subject = ?, body = ? WHERE id = ? AND status = 'draft'",
            (subject, body, outbound_id),
        )

    def approve_outbound(self, outbound_id: int, approved_by: str) -> None:
        """Approval is the end of the road: the message is only ever 'sent' as a simulation."""
        self._write(
            "UPDATE outbound_messages SET status = 'sent_simulated', approved_by = ?, sent_at = ? "
            "WHERE id = ? AND status = 'draft'",
            (approved_by, now_iso(), outbound_id),
        )
