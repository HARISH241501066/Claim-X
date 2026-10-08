"""Passwords, tokens and the login throttle.

Passwords are hashed with bcrypt. A token is an HS256 JWT signed with JWT_SECRET (an environment
variable or the git-ignored .env, never the code) and valid for 8 hours. It names the user, the
role and the unit, but the API re-reads the user on every request, so a deactivated account or a
changed role takes effect at once.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from backend.access.store import User
from backend.brief.generate import resolve_setting

TOKEN_HOURS = 8
ALGORITHM = "HS256"
MIN_SECRET_LENGTH = 32
MIN_PASSWORD_LENGTH = 8
MAX_FAILURES = 5
LOCK_SECONDS = 300
# A real bcrypt hash of a throwaway string, checked when the username is unknown so that
# "no such user" and "wrong password" take the same time.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt(rounds=4))


class NotConfiguredError(RuntimeError):
    """Sign-in cannot work because JWT_SECRET is missing or too short."""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str | None) -> bool:
    target = hashed.encode("ascii") if hashed else _DUMMY_HASH
    try:
        ok = bcrypt.checkpw(password.encode("utf-8")[:72], target)
    except ValueError:
        return False
    return ok and hashed is not None


def _secret(env: Mapping[str, str] | None = None) -> str:
    secret = resolve_setting("JWT_SECRET", env)
    if len(secret) < MIN_SECRET_LENGTH:
        raise NotConfiguredError(
            f"Sign-in is not configured: JWT_SECRET must be set to at least {MIN_SECRET_LENGTH} characters."
        )
    return secret


def create_token(user: User, env: Mapping[str, str] | None = None) -> tuple[str, int]:
    now = datetime.now(UTC)
    claims = {
        "sub": str(user.id), "role": user.role, "unit_id": user.unit_id,
        "iat": now, "exp": now + timedelta(hours=TOKEN_HOURS),
    }  # fmt: skip
    return jwt.encode(claims, _secret(env), algorithm=ALGORITHM), TOKEN_HOURS * 3600


def decode_token(token: str, env: Mapping[str, str] | None = None) -> dict | None:
    """The claims of a valid, unexpired token, or None."""
    try:
        return jwt.decode(token, _secret(env), algorithms=[ALGORITHM], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return None


class LoginThrottle:
    """Five wrong passwords for one username lock that username for five minutes."""

    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def locked_for(self, username: str) -> int:
        """Seconds left on a lock, or 0."""
        with self._lock:
            recent = [t for t in self._failures.get(username.lower(), []) if time.monotonic() - t < LOCK_SECONDS]
            self._failures[username.lower()] = recent
            if len(recent) >= MAX_FAILURES:
                return max(1, int(LOCK_SECONDS - (time.monotonic() - recent[0])))
            return 0

    def fail(self, username: str) -> None:
        with self._lock:
            self._failures.setdefault(username.lower(), []).append(time.monotonic())

    def clear(self, username: str) -> None:
        with self._lock:
            self._failures.pop(username.lower(), None)
