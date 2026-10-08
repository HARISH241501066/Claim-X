"""Units, users and case assignments, kept in the audit database file.

Passwords are stored only as bcrypt hashes. A case has at most one assignment row: it names the
unit the case is routed to and, once a team lead assigns it, the investigator who owns it.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from backend.audit import now_iso

ROLES = ("admin", "team_lead", "investigator")
CASE_STATUSES = ("unassigned", "assigned", "in_review", "closed")

DDL = f"""
CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
    region TEXT NOT NULL DEFAULT '[]');  -- a JSON list of the provider cities the unit covers

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN {ROLES}),
    unit_id INTEGER REFERENCES units(id),
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    CHECK ((role = 'admin' AND unit_id IS NULL) OR (role <> 'admin' AND unit_id IS NOT NULL)));
-- a unit has one team lead
CREATE UNIQUE INDEX IF NOT EXISTS one_active_lead_per_unit
    ON users (unit_id) WHERE role = 'team_lead' AND active = 1;

CREATE TABLE IF NOT EXISTS case_assignments (
    case_id TEXT PRIMARY KEY, unit_id INTEGER REFERENCES units(id),
    assignee_user_id INTEGER REFERENCES users(id), assigned_by INTEGER REFERENCES users(id),
    assigned_at TEXT, status TEXT NOT NULL CHECK (status IN {CASE_STATUSES}), reason TEXT);

-- Read state is per person: the same notification can be seen by a lead and an investigator.
CREATE TABLE IF NOT EXISTS notification_reads (
    user_id INTEGER NOT NULL, notification_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, notification_id));
"""


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: str
    unit_id: int | None
    active: bool = True


class DuplicateError(ValueError):
    """A unique rule was broken (a username in use, a second team lead for a unit)."""


class AccessStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as con, con:
            con.executescript(DDL)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        con = self._connect()
        try:
            return [dict(r) for r in con.execute(sql, params).fetchall()]
        finally:
            con.close()

    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    def _write(self, sql: str, params: tuple = ()) -> int:
        con = self._connect()
        try:
            with con:
                return con.execute(sql, params).lastrowid or 0
        finally:
            con.close()

    # ------------------------------------------------------------ units

    def create_unit(self, name: str, region: list[str]) -> dict:
        try:
            new_id = self._write("INSERT INTO units (name, region) VALUES (?, ?)",
                                 (name, json.dumps(region)))  # fmt: skip
        except sqlite3.IntegrityError as exc:
            raise DuplicateError(f"A unit named {name} already exists.") from exc
        return self.get_unit(new_id)

    @staticmethod
    def _unit(row: dict | None) -> dict | None:
        if row:
            row["region"] = json.loads(row["region"])
        return row

    def get_unit(self, unit_id: int) -> dict | None:
        return self._unit(self._one("SELECT * FROM units WHERE id = ?", (unit_id,)))

    def list_units(self) -> list[dict]:
        return [self._unit(r) for r in self._all("SELECT * FROM units ORDER BY id")]

    def set_region(self, unit_id: int, region: list[str]) -> None:
        self._write("UPDATE units SET region = ? WHERE id = ?", (json.dumps(region), unit_id))

    # ------------------------------------------------------------ users

    def create_user(
        self, *, username: str, display_name: str, role: str, unit_id: int | None, password_hash: str
    ) -> User:  # fmt: skip
        try:
            new_id = self._write(
                "INSERT INTO users (username, display_name, role, unit_id, password_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (username, display_name, role, unit_id, password_hash),
            )
        except sqlite3.IntegrityError as exc:
            if "one_active_lead" in str(exc) or "UNIQUE constraint failed: users.unit_id" in str(exc):
                raise DuplicateError("That unit already has a team lead.") from exc
            if "users.username" in str(exc):
                raise DuplicateError(f"The username {username} is already in use.") from exc
            raise ValueError("That role needs a unit, and an admin has none.") from exc
        return self.get_user(new_id)

    @staticmethod
    def _user(row: dict | None) -> User | None:
        if not row:
            return None
        return User(row["id"], row["username"], row["display_name"], row["role"], row["unit_id"],
                    bool(row["active"]))  # fmt: skip

    def get_user(self, user_id: int) -> User | None:
        return self._user(self._one("SELECT * FROM users WHERE id = ?", (user_id,)))

    def get_login(self, username: str) -> tuple[User, str] | None:
        """The user and their password hash, for checking a login."""
        row = self._one("SELECT * FROM users WHERE username = ?", (username,))
        return (self._user(row), row["password_hash"]) if row else None

    def list_users(self, unit_id: int | None = None) -> list[User]:
        sql, params = "SELECT * FROM users", ()
        if unit_id is not None:
            sql, params = sql + " WHERE unit_id = ?", (unit_id,)
        return [self._user(r) for r in self._all(sql + " ORDER BY id", params)]

    def user_count(self) -> int:
        return self._one("SELECT COUNT(*) AS n FROM users")["n"]

    def set_active(self, user_id: int, active: bool) -> None:
        try:
            self._write("UPDATE users SET active = ? WHERE id = ?", (int(active), user_id))
        except sqlite3.IntegrityError as exc:
            raise DuplicateError("That unit already has an active team lead.") from exc

    # ------------------------------------------------------------ assignments

    def get_assignment(self, case_id: str) -> dict | None:
        return self._one("SELECT * FROM case_assignments WHERE case_id = ?", (case_id,))

    def assignments(self, unit_id: int | None = None) -> dict[str, dict]:
        sql, params = "SELECT * FROM case_assignments", ()
        if unit_id is not None:
            sql, params = sql + " WHERE unit_id = ?", (unit_id,)
        return {r["case_id"]: r for r in self._all(sql, params)}

    def route(self, case_id: str, unit_id: int | None) -> None:
        """Place a case in a unit (or leave it unrouted). Never touches an existing assignee."""
        self._write(
            "INSERT INTO case_assignments (case_id, unit_id, status) VALUES (?, ?, 'unassigned') "
            "ON CONFLICT(case_id) DO UPDATE SET unit_id = excluded.unit_id "
            "WHERE case_assignments.unit_id IS NULL AND case_assignments.assignee_user_id IS NULL",
            (case_id, unit_id),
        )

    def assign(self, case_id: str, assignee_id: int, by_id: int, reason: str) -> None:
        self._write(
            "UPDATE case_assignments SET assignee_user_id = ?, assigned_by = ?, assigned_at = ?, "
            "status = 'assigned', reason = ? WHERE case_id = ?",
            (assignee_id, by_id, now_iso(), reason, case_id),
        )

    def unassign(self, case_id: str, by_id: int, reason: str) -> None:
        self._write(
            "UPDATE case_assignments SET assignee_user_id = NULL, assigned_by = ?, assigned_at = ?, "
            "status = 'unassigned', reason = ? WHERE case_id = ?",
            (by_id, now_iso(), reason, case_id),
        )

    def set_status(self, case_id: str, status: str) -> None:
        self._write("UPDATE case_assignments SET status = ? WHERE case_id = ?", (status, case_id))

    def unrouted(self) -> list[str]:
        return [r["case_id"] for r in self._all(
            "SELECT case_id FROM case_assignments WHERE unit_id IS NULL ORDER BY case_id")]  # fmt: skip

    # ------------------------------------------------------------ per-person read state

    def read_ids(self, user_id: int) -> set[int]:
        return {r["notification_id"] for r in self._all(
            "SELECT notification_id FROM notification_reads WHERE user_id = ?", (user_id,))}  # fmt: skip

    def mark_read(self, user_id: int, notification_ids: list[int]) -> int:
        con = self._connect()
        try:
            with con:
                before = con.total_changes
                con.executemany(
                    "INSERT OR IGNORE INTO notification_reads (user_id, notification_id) VALUES (?, ?)",
                    [(user_id, n) for n in notification_ids],
                )
                return con.total_changes - before
        finally:
            con.close()
