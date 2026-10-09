"""Synthetic demo units and users. Every seeded user gets the password from DEMO_PASSWORD.

Nothing about a password lives in the code: if DEMO_PASSWORD is missing or too short, no user is
created and sign-in reports that it is not set up.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from backend.access.security import MIN_PASSWORD_LENGTH, hash_password
from backend.access.store import AccessStore
from backend.brief.generate import resolve_setting

log = logging.getLogger("claimx.access")

DEMO_UNITS = {
    "Unit South": ["Chennai", "Bengaluru", "Hyderabad"],
    "Unit North": ["Delhi", "Mumbai", "Kolkata"],
}
# (username, display name, role, unit, ...) — synthetic people
DEMO_USERS = [
    ("admin", "System Admin", "admin", None),
    ("south_lead", "Kavya Menon", "team_lead", "Unit South"),
    ("south_inv1", "Arjun Nair", "investigator", "Unit South"),
    ("south_inv2", "Divya Reddy", "investigator", "Unit South"),
    ("north_lead", "Rohit Sharma", "team_lead", "Unit North"),
    ("north_inv1", "Neha Gupta", "investigator", "Unit North"),
    ("north_inv2", "Imran Khan", "investigator", "Unit North"),
]  # fmt: skip

Seeder = Callable[[AccessStore, str], None]


def seed_demo(store: AccessStore, password: str) -> None:
    """Create the demo units and users (only called when no user exists yet)."""
    units = {name: store.create_unit(name, cities)["id"] for name, cities in DEMO_UNITS.items()}
    hashed = hash_password(password)  # one hash is enough: the same demo password for everyone
    for username, display, role, unit in DEMO_USERS:
        store.create_user(username=username, display_name=display, role=role,
                          unit_id=units.get(unit), password_hash=hashed)  # fmt: skip


def seed_single_unit(store: AccessStore, password: str) -> None:
    """For tests: one unit covering every city, so any case can be reached by its team lead."""
    unit = store.create_unit("Unit All", ["Chennai", "Bengaluru", "Hyderabad", "Delhi", "Mumbai", "Kolkata"])
    hashed = hash_password(password)
    for username, display, role, _ in DEMO_USERS:
        if role != "admin" and username.startswith("north"):
            continue
        store.create_user(username=username, display_name=display, role=role,
                          unit_id=None if role == "admin" else unit["id"], password_hash=hashed)  # fmt: skip


def ensure_seeded(store: AccessStore, seeder: Seeder = seed_demo, env: Mapping[str, str] | None = None) -> bool:
    """Seed once, on an empty users table. Returns whether users exist afterwards."""
    if store.user_count():
        return True
    password = resolve_setting("DEMO_PASSWORD", env)
    if len(password) < MIN_PASSWORD_LENGTH:
        log.warning("DEMO_PASSWORD is not set (or shorter than %d): no demo users were created",
                    MIN_PASSWORD_LENGTH)  # fmt: skip
        return False
    seeder(store, password)
    log.info("seeded the demo units and users")
    return True
