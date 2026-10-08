import pytest

from backend.access.seed import seed_single_unit
from backend.tests.auth_helpers import PASSWORD, SECRET


@pytest.fixture(autouse=True)
def safe_environment(monkeypatch):
    """No test may publish to a real AWS topic or sign in with a real secret, whatever .env says."""
    monkeypatch.setenv("NOTIFY_EMAIL_ENABLED", "false")
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("DEMO_PASSWORD", PASSWORD)
    # most tests want one unit that can reach every case; the RBAC tests pass seed_demo themselves
    monkeypatch.setattr("backend.api.main.seed_demo", seed_single_unit)
