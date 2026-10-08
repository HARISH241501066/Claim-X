import socket

import pytest

from backend.access.seed import seed_single_unit
from backend.tests.auth_helpers import PASSWORD, SECRET

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0", "", None}


class NetworkBlockedError(RuntimeError):
    """A test tried to reach a machine other than this one."""


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test may call a real API: only this machine can be reached, so a mock that is missing
    fails loudly instead of quietly spending money or sending an email."""
    connect, connect_ex, getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def host_of(address):
        return address[0] if isinstance(address, tuple) else None  # a path is a local socket

    def refuse(host):
        raise NetworkBlockedError(f"network access is blocked in tests (tried to reach {host})")

    def guarded_connect(self, address, *args, **kwargs):
        if host_of(address) not in LOCAL_HOSTS:
            refuse(host_of(address))
        return connect(self, address, *args, **kwargs)

    def guarded_connect_ex(self, address, *args, **kwargs):
        if host_of(address) not in LOCAL_HOSTS:
            refuse(host_of(address))
        return connect_ex(self, address, *args, **kwargs)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host not in LOCAL_HOSTS:
            refuse(host)
        return getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


@pytest.fixture(autouse=True)
def safe_environment(monkeypatch):
    """No test may publish to a real AWS topic, call a real LLM or sign in with a real secret,
    whatever the developer's .env says."""
    monkeypatch.setenv("NOTIFY_EMAIL_ENABLED", "false")
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("DEMO_PASSWORD", PASSWORD)
    # most tests want one unit that can reach every case; the RBAC tests pass seed_demo themselves
    monkeypatch.setattr("backend.api.main.seed_demo", seed_single_unit)
