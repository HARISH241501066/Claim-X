"""Hardening checks: nothing reaches the network, every rule has a planted scenario, no secrets or
undocumented settings creep in, and the main endpoints answer. Most of the behaviour checks live
beside their module (see docs/TESTING.md for the map); this file holds the cross-cutting ones."""

import re
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import pipeline
from backend.api.main import create_app
from backend.brief.generate import resolve_setting
from backend.tests.auth_helpers import login

REPO = Path(__file__).resolve().parents[2]
RULES = {
    "duplicate", "impossible_timing", "phantom", "repeat_history", "unbundling", "upcoding", "utilization",
}  # fmt: skip
SKIP_DIRS = {".venv", "node_modules", ".git", "__pycache__", ".pytest_cache", ".ruff_cache", "dist", "coverage"}


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    return pipeline.run_all(tmp_path_factory.mktemp("m10") / "claimshield.db")


def tracked_files() -> list[Path]:
    """Files git tracks (or, without git, every file outside the usual build folders)."""
    if shutil.which("git") and (REPO / ".git").exists():
        out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True)
        return [REPO / line for line in out.stdout.splitlines() if (REPO / line).is_file()]
    return [p for p in REPO.rglob("*") if p.is_file() and not SKIP_DIRS & set(p.relative_to(REPO).parts)]


# ------------------------------------------------------------ no real network, no real provider


def test_a_test_cannot_reach_the_internet():
    with pytest.raises(RuntimeError, match="network access is blocked.*example.com"):
        socket.create_connection(("example.com", 443), timeout=1)
    with pytest.raises(RuntimeError, match="network access is blocked"):
        socket.getaddrinfo("sns.eu-north-1.amazonaws.com", 443)
    with pytest.raises(RuntimeError, match="network access is blocked"):
        socket.socket().connect(("203.0.113.9", 80))


def test_tests_run_with_no_llm_provider_and_no_email(monkeypatch):
    assert resolve_setting("LLM_PROVIDER", None) == "none"
    assert resolve_setting("NOTIFY_EMAIL_ENABLED", None) == "false"


# ------------------------------------------------------------ the rules and the planted scenarios


def test_each_of_the_seven_rules_fires_on_its_planted_scenario(shared):
    by = {}
    for f in shared.findings:
        by.setdefault(f["detector"], []).append(f)
    assert RULES <= set(by), f"no finding from {RULES - set(by)}"
    assert {f["entity_id"] for f in by["upcoding"]} == {"PRV-005"}
    assert {f["entity_id"] for f in by["repeat_history"]} == {"PRV-010"}
    assert {f["entity_id"] for f in by["impossible_timing"] if f["entity_id"].startswith("PRV-")} == {"PRV-029"}


def test_the_honest_busy_specialist_is_flagged_by_no_rule(shared):
    honest = "PRV-015"
    rule_findings = [f for f in shared.findings if f["detector"] in RULES]
    assert honest not in {f["entity_id"] for f in rule_findings}
    assert all(honest not in c.entity_ids for c in shared.cases)  # and there is no case for it


# ------------------------------------------------------------ the main endpoints answer


def test_the_main_endpoints_return_200(shared, tmp_path):
    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "audit.db", runner=lambda p: shared)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200  # open
        login(client, "south_lead")
        case = client.get("/queue").json()["scheduled"][0]["case_id"]
        for path in ("/overview", "/queue", f"/cases/{case}", f"/cases/{case}/graph",
                     f"/cases/{case}/brief", "/audit"):  # fmt: skip
            assert client.get(path).status_code == 200, path
        brief = client.get(f"/cases/{case}/brief").json()
        assert brief["source"] == "template" and brief["fallback_reason"]  # no provider in tests


# ------------------------------------------------------------ secrets and settings hygiene

KEY_PATTERNS = {
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Groq key": re.compile(r"\bgsk_[A-Za-z0-9]{20,}"),
    "xAI key": re.compile(r"\bxai-[A-Za-z0-9]{20,}"),
    "API secret key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
# fixed fake values used by tests of the secret handling itself
FAKE_KEYS = {"sk-ant-TEST-secret-key-do-not-log"}


def test_no_real_looking_key_is_tracked_in_git():
    hits = []
    for path in tracked_files():
        if path.suffix in {".png", ".jpg", ".pdf", ".ico", ".woff", ".woff2"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in KEY_PATTERNS.items():
            for match in pattern.findall(text):
                if match not in FAKE_KEYS:
                    hits.append(f"{path.relative_to(REPO)}: {label}")
    assert hits == []


def test_no_env_file_or_database_is_tracked():
    names = [str(p.relative_to(REPO)).replace("\\", "/") for p in tracked_files()]
    assert [n for n in names if n == ".env" or n.endswith(("/.env", ".db", ".sqlite"))] == []
    assert ".env.example" in names


def test_gitignore_covers_the_files_that_must_never_be_committed():
    lines = {line.strip() for line in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()}
    for needed in (".venv/", "node_modules/", "*.db", ".env", "backend/data/kaggle/"):
        assert needed in lines, f".gitignore must list {needed}"


def settings_read_by_the_code() -> set[str]:
    names = set()
    for path in (REPO / "backend").rglob("*.py"):
        if SKIP_DIRS & set(path.relative_to(REPO).parts) or "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        names |= set(re.findall(r'resolve_setting\(\s*"([A-Z_]+)"', text))
        names |= set(re.findall(r'os\.environ\.get\(\s*"([A-Z_]+)"', text))
    names |= set(re.findall(r"VITE_[A-Z_]+", "\n".join(
        p.read_text(encoding="utf-8") for p in (REPO / "frontend" / "src").rglob("*.js*")
        if "node_modules" not in p.parts)))  # fmt: skip
    return names


def test_every_setting_the_code_reads_is_in_env_example_blank_with_a_comment():
    lines = (REPO / ".env.example").read_text(encoding="utf-8").splitlines()
    for name in sorted(settings_read_by_the_code()):
        matches = [i for i, line in enumerate(lines) if line.startswith(f"{name}=")]
        assert matches, f"{name} is read by the code but missing from .env.example"
        i = matches[0]
        assert lines[i] == f"{name}=", f"{name} must be empty in .env.example"
        assert i > 0 and lines[i - 1].startswith("#"), f"{name} needs a one-line comment above it"


def test_env_example_only_lists_settings_the_code_reads():
    listed = {line.split("=")[0] for line in (REPO / ".env.example").read_text(encoding="utf-8").splitlines()
              if re.match(r"^[A-Z_]+=", line)}  # fmt: skip
    extra = listed - settings_read_by_the_code() - {"AWS_SESSION_TOKEN"}
    assert extra == set(), f"documented but never read: {extra}"


def test_the_readme_has_no_keys_and_the_expected_sections():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    for label, pattern in KEY_PATTERNS.items():
        assert not pattern.search(text), f"README contains a {label}"
    assert not re.search(r"\barn:aws:\w+:[a-z0-9-]*:\d{12}:", text), "README must not contain an account id"
    for heading in ("Quick start", "Demo walkthrough", "Detection approaches", "Results", "Responsible AI",
                    "Known limitations", "Tech stack"):  # fmt: skip
        assert re.search(rf"^#+ .*{heading}", text, re.MULTILINE | re.IGNORECASE), heading
    assert "```mermaid" in text and "actions/workflows/ci.yml/badge.svg" in text
