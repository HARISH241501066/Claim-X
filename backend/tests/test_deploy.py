"""One-address deployment: the API serves the built web app, and the deploy files are safe."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import pipeline
from backend.api.main import create_app
from backend.tests.auth_helpers import PASSWORD, login

REPO = Path(__file__).resolve().parents[2]
PAGE = {"accept": "text/html,application/xhtml+xml,*/*;q=0.8"}  # what a browser sends when opening a page


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    return pipeline.run_all(tmp_path_factory.mktemp("deploy") / "claimx.db")


@pytest.fixture
def dist(tmp_path):
    out = tmp_path / "dist"
    (out / "assets").mkdir(parents=True)
    (out / "index.html").write_text("<!doctype html><title>Claim-X</title><div id=root></div>", encoding="utf-8")
    (out / "assets" / "app-abc123.js").write_text("console.log('app')", encoding="utf-8")
    (out / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the web app", encoding="utf-8")
    return out


@pytest.fixture
def client(shared, tmp_path, dist):
    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "audit.db", runner=lambda p: shared, static_dir=dist)
    with TestClient(app) as c:
        yield c


def test_a_browser_opening_any_page_gets_the_web_app(client):
    for path in ("/", "/login", "/queue", "/my-cases", "/cases/CASE-0001", "/some/unknown/page"):
        r = client.get(path, headers=PAGE)
        assert r.status_code == 200 and "<title>Claim-X</title>" in r.text, path
        assert r.headers["cache-control"] == "no-store"


def test_the_apps_own_calls_still_reach_the_api_even_where_a_page_has_the_same_name(client):
    assert client.get("/queue", headers={"accept": "application/json, text/plain, */*"}).status_code == 401
    assert client.get("/queue").status_code == 401  # no Accept header: the API
    login(client, "admin")
    assert "scheduled" in client.get("/queue", headers={"accept": "application/json"}).json()
    assert client.get("/cases/CASE-0001", headers={"accept": "application/json"}).json()["case_id"] == "CASE-0001"


def test_the_api_pages_stay_the_api_for_a_browser_too(client):
    assert client.get("/health", headers=PAGE).json()["ready"] is True
    assert "swagger" in client.get("/docs", headers=PAGE).text.lower()
    assert client.get("/openapi.json", headers=PAGE).json()["info"]["title"] == "Claim-X API"


def test_built_files_are_served_with_long_caching_only_under_assets(client):
    js = client.get("/assets/app-abc123.js")
    assert js.status_code == 200 and "console.log" in js.text and "immutable" in js.headers["cache-control"]
    icon = client.get("/favicon.svg")
    assert icon.status_code == 200 and icon.headers["cache-control"] == "no-store"


def test_a_file_outside_the_web_app_can_never_be_read(client):
    for path in ("/../secret.txt", "/assets/../../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt"):
        r = client.get(path)
        assert "outside the web app" not in r.text, path


def test_posts_are_never_taken_by_the_web_app(client):
    r = client.post("/auth/login", json={"username": "nobody", "password": "wrong-password"}, headers=PAGE)
    assert r.status_code == 401 and r.json()["detail"] == "Wrong username or password."
    assert client.post("/auth/login", json={"username": "admin", "password": PASSWORD}, headers=PAGE).status_code == 200


def test_without_a_build_or_without_asking_nothing_is_served(shared, tmp_path, monkeypatch):
    plain = create_app(data_dir=tmp_path, audit_path=tmp_path / "a.db", runner=lambda p: shared)
    with TestClient(plain) as c:  # not asked for: an ordinary API
        assert c.get("/", headers=PAGE).status_code == 404
    empty = tmp_path / "nothing"
    empty.mkdir()
    missing = create_app(data_dir=tmp_path, audit_path=tmp_path / "b.db", runner=lambda p: shared, static_dir=empty)
    with TestClient(missing) as c:  # asked for, but never built: the API only, no crash
        assert c.get("/", headers=PAGE).status_code == 404 and c.get("/health").status_code == 200
    monkeypatch.setenv("CLAIMX_SERVE_FRONTEND", "true")
    from backend.api import static

    monkeypatch.setattr(static, "DEFAULT_DIST", tmp_path / "dist-from-setting")
    (tmp_path / "dist-from-setting").mkdir()
    (tmp_path / "dist-from-setting" / "index.html").write_text("<title>from setting</title>", encoding="utf-8")
    via_setting = create_app(data_dir=tmp_path, audit_path=tmp_path / "c.db", runner=lambda p: shared)
    with TestClient(via_setting) as c:
        assert "from setting" in c.get("/anything", headers=PAGE).text


def test_extra_web_addresses_can_be_allowed_by_setting(shared, tmp_path, monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://claim-x.example.com/, https://other.example.org")
    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "a.db", runner=lambda p: shared)
    with TestClient(app) as c:
        allowed = c.get("/health", headers={"Origin": "https://claim-x.example.com"})
        assert allowed.headers["access-control-allow-origin"] == "https://claim-x.example.com"
        assert "access-control-allow-origin" in c.get("/health", headers={"Origin": "https://other.example.org"}).headers
        assert "access-control-allow-origin" not in c.get("/health", headers={"Origin": "https://evil.example"}).headers


def test_the_dockerfile_builds_the_web_app_serves_it_and_bakes_in_no_secret():
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "npm run build" in text and "COPY --from=web /web/dist frontend/dist" in text
    assert "CLAIMX_SERVE_FRONTEND=true" in text and "USER app" in text and "${PORT:-8000}" in text
    assert "ENV VITE_API_URL=\n" in text  # an empty address: the app calls the page's own origin
    instructions = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"(JWT_SECRET|DEMO_PASSWORD|API_KEY|SECRET_ACCESS_KEY)[= ]\S", instructions)
    ignored = (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for never in (".env", "backend/*.db", "backend/audit.archive", "frontend/node_modules"):
        assert never in ignored


def test_render_blueprint_keeps_secrets_out_of_git():
    text = (REPO / "render.yaml").read_text(encoding="utf-8")
    assert "runtime: docker" in text and "healthCheckPath: /health" in text
    jwt = text.split("key: JWT_SECRET")[1].split("- key:")[0]
    assert "generateValue: true" in jwt and "value:" not in jwt.replace("generateValue", "")
    demo = text.split("key: DEMO_PASSWORD")[1].split("- key:")[0]
    assert "sync: false" in demo and "value:" not in demo
    assert re.search(r"key: LLM_PROVIDER\s+value: none", text)
    assert re.search(r'key: NOTIFY_EMAIL_ENABLED\s+value: "false"', text)
