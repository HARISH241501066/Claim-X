"""make reset: archive the audit log, start clean, prewarm and open the top cases, send no email."""

import os
import socket
import sqlite3

import pytest

from backend import demo_reset, pipeline
from backend.audit import AuditLog
from backend.notify import notifier


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    return pipeline.run_all(tmp_path_factory.mktemp("reset") / "claimx.db")


@pytest.fixture
def folders(tmp_path):
    return {"audit_path": tmp_path / "audit.db", "data_dir": tmp_path, "archive_dir": tmp_path / "archive"}


def run(shared, folders, **kw):
    return demo_reset.reset(regenerate=False, runner=lambda path: shared, **folders, **kw)


def test_reset_archives_the_old_audit_log_and_starts_a_clean_one(shared, folders):
    old = AuditLog(folders["audit_path"])
    old.append("decision", case_id="CASE-0001", action="monitor", reason="From an earlier demo", reviewer="Old Reviewer")
    summary = run(shared, folders)
    archived = sqlite3.connect(summary["archived_audit"])
    try:
        assert archived.execute("SELECT reason FROM audit_log WHERE event_type = 'decision'").fetchone() == ("From an earlier demo",)
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):  # the archive is still tamper-proof
            archived.execute("DELETE FROM audit_log")
    finally:
        archived.close()
    fresh = AuditLog(folders["audit_path"])
    assert fresh.list(1000, event_type="decision") == [] and fresh.latest_decisions() == {}
    assert fresh.list(10, event_type="pipeline_run")  # the new run is logged


def test_reset_leaves_a_demo_ready_state(shared, folders):
    summary = run(shared, folders)
    assert summary["cases"] == len(shared.cases) and summary["notifications"] > 0
    assert summary["prewarm"]["requested"] == 5 and len(summary["opened"]) == 5
    assert all(o["case"] == 200 and o["brief"] == 200 for o in summary["opened"])
    assert all(o["badge"] == "Template" for o in summary["opened"])  # no provider is configured in tests
    ranks = [i["rank"] for i in summary["prewarm"]["items"]]
    assert ranks == sorted(ranks) and ranks[0] == 1
    # the notifications start unread and the brief cache holds nothing left over
    store = AuditLog(folders["audit_path"])
    assert store.list_cached_briefs() == []


def test_a_second_reset_archives_the_first_one_too(shared, folders):
    run(shared, folders)
    run(shared, folders)
    assert len(list(folders["archive_dir"].glob("audit-*.db"))) == 1  # the first run had nothing to archive
    run(shared, folders)
    assert len(list(folders["archive_dir"].glob("audit-*.db"))) == 2


def test_reset_sends_no_email_unless_asked(shared, folders, monkeypatch):
    sent = []

    class Sns:
        def publish(self, **kw):
            sent.append(kw)

    monkeypatch.setattr(notifier.boto3, "client", lambda *a, **k: Sns())
    monkeypatch.setenv("NOTIFY_EMAIL_ENABLED", "true")
    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:eu-north-1:000000000000:demo")
    summary = run(shared, folders)
    assert sent == [] and summary["emails_enabled"] is False
    assert os.environ["NOTIFY_EMAIL_ENABLED"] == "true"  # the setting is put back
    run(shared, folders, email=True)
    assert len(sent) > 0 and all("CASE-" in m["Subject"] for m in sent)


def test_reset_explains_what_is_missing_when_sign_in_is_not_set_up(shared, folders, monkeypatch):
    monkeypatch.setattr(demo_reset, "resolve_setting", lambda name, env=None: "")
    with pytest.raises(demo_reset.ResetError, match="JWT_SECRET.*DEMO_PASSWORD"):
        run(shared, folders)
    assert not (folders["archive_dir"]).exists()  # nothing was moved before the check


def test_reset_refuses_to_run_while_the_api_is_running(folders, capsys):
    folders["audit_path"].write_bytes(b"")  # something that must not be moved
    with socket.socket() as listener:  # stands in for a running API
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        assert demo_reset.api_is_running(listener.getsockname()[1]) is True
        assert demo_reset.main(["--api-port", str(listener.getsockname()[1])]) == 1
    assert "the API is running" in capsys.readouterr().err
    assert demo_reset.api_is_running(1) is False  # nothing listens on port 1
    assert folders["audit_path"].exists() and not folders["archive_dir"].exists()
