import pytest


@pytest.fixture(autouse=True)
def no_real_email(monkeypatch):
    """A test must never publish to a real AWS topic, whatever the developer's .env says."""
    monkeypatch.setenv("NOTIFY_EMAIL_ENABLED", "false")
