"""Sign-in helpers for tests. The password and secret are test-only values set by conftest."""

from fastapi.testclient import TestClient

from backend.access.seed import seed_single_unit

PASSWORD = "test-password-123"
SECRET = "test-secret-" + "0123456789abcdef" * 3
seed = seed_single_unit  # one unit covering every city: the lead can reach any case


def login(client: TestClient, username: str = "south_lead", password: str = PASSWORD) -> str:
    """Sign in and make this client send the token from now on. Returns the token."""
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return token


def as_user(client: TestClient, username: str) -> TestClient:
    login(client, username)
    return client
