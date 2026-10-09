"""Put the platform in a clean, demo-ready state:  python -m backend.demo_reset  (or: make reset).

It moves the old audit database aside (the audit log is append-only, so it is archived, never
edited or deleted), regenerates the synthetic data, reruns the pipeline, seeds the demo users,
starts with no notifications and an empty brief cache, and prewarms the briefs of the top cases.
It sends no email unless asked (--email), so a reset never surprises an inbox.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.main import BACKEND_DIR, Runner, create_app
from backend.audit import AUDIT_PATH
from backend.brief.generate import resolve_setting
from backend.data import generator

log = logging.getLogger("claimx.reset")
ARCHIVE_DIR = BACKEND_DIR / "audit.archive"
ADMIN = "admin"


class ResetError(RuntimeError):
    """The reset could not finish; the message says what to fix."""


def api_is_running(port: int = 8000) -> bool:
    """True if something is already listening on the API port on this machine."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def archive_audit(audit_path: Path, archive_dir: Path = ARCHIVE_DIR) -> Path | None:
    """Move the audit database (and its journal files) aside. Returns where it went, if it existed."""
    if not audit_path.exists():
        return None
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / f"audit-{datetime.now(UTC):%Y%m%d-%H%M%S-%f}.db"
    shutil.move(str(audit_path), target)
    for suffix in ("-wal", "-shm", "-journal"):
        side = Path(f"{audit_path}{suffix}")
        if side.exists():
            shutil.move(str(side), f"{target}{suffix}")
    return target


def reset(
    *,
    top: int = 5,
    email: bool = False,
    audit_path: Path | None = None,
    data_dir: Path = BACKEND_DIR,
    archive_dir: Path = ARCHIVE_DIR,
    runner: Runner | None = None,
    regenerate: bool = True,
) -> dict:
    """Reset and return a summary: archive location, case counts and the prewarm result."""
    audit_file = Path(audit_path or os.environ.get("CLAIMX_AUDIT_PATH") or AUDIT_PATH)
    if len(resolve_setting("JWT_SECRET", None)) < 32 or len(resolve_setting("DEMO_PASSWORD", None)) < 8:
        raise ResetError("Set JWT_SECRET (32+ characters) and DEMO_PASSWORD (8+) in .env first; see .env.example.")
    password = resolve_setting("DEMO_PASSWORD", None)

    archived = archive_audit(audit_file, archive_dir)
    for name in ("claimx.db", "claimx.alt.db"):
        (Path(data_dir) / name).unlink(missing_ok=True)
    if regenerate:
        generator.generate(Path(data_dir) / "claimx.db", None)  # the ground truth is for tests only

    # the email channel stays off during a reset unless it was asked for
    previous = os.environ.get("NOTIFY_EMAIL_ENABLED")
    if not email:
        os.environ["NOTIFY_EMAIL_ENABLED"] = "false"
    try:
        app = create_app(data_dir=Path(data_dir), audit_path=audit_file, runner=runner)
        with TestClient(app) as client:
            token = client.post("/auth/login", json={"username": ADMIN, "password": password})
            if token.status_code != 200:
                raise ResetError(f"Could not sign in as {ADMIN}: {token.json().get('detail')}")
            auth = {"Authorization": f"Bearer {token.json()['access_token']}"}
            prewarm = client.post("/admin/prewarm-briefs", params={"top": top}, headers=auth)
            if prewarm.status_code != 200:
                raise ResetError(f"Prewarm failed ({prewarm.status_code}): {prewarm.text[:200]}")
            warmed = prewarm.json()
            opened = []
            for item in warmed["items"]:  # open each case the way the demo will
                got = client.get(f"/cases/{item['case_id']}", headers=auth)
                brief = client.get(f"/cases/{item['case_id']}/brief", headers=auth)
                opened.append({"case_id": item["case_id"], "case": got.status_code, "brief": brief.status_code,
                               "badge": brief.json().get("provider_label") if brief.status_code == 200 else None,
                               "cached": brief.json().get("cached") if brief.status_code == 200 else None})  # fmt: skip
            runtime = app.state.runtime
            summary = {
                "archived_audit": str(archived) if archived else None,
                "cases": len(runtime.state.cases),
                "notifications": len(runtime.notify.list_notifications(None, False, 1000)),
                "stored_briefs": len(runtime.audit.list_cached_briefs()),
                "prewarm": warmed, "opened": opened, "emails_enabled": email,
            }
    finally:
        if previous is None:
            os.environ.pop("NOTIFY_EMAIL_ENABLED", None)
        else:
            os.environ["NOTIFY_EMAIL_ENABLED"] = previous
    bad = [o for o in summary["opened"] if o["case"] != 200 or o["brief"] != 200]
    if bad:
        raise ResetError(f"These cases did not open cleanly: {[o['case_id'] for o in bad]}")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reset the platform to a clean, demo-ready state.")
    parser.add_argument("--top", type=int, default=5, help="how many top cases get a prewarmed brief (1-10)")
    parser.add_argument("--email", action="store_true", help="also send the high-priority emails (real SNS)")
    parser.add_argument("--api-port", type=int, default=8000, help="the port the API would run on")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if api_is_running(args.api_port):  # a running API keeps old decisions in memory and would write to the new log
        print(f"Reset stopped: the API is running on port {args.api_port}. Stop it (Ctrl+C in the make api window) and try again.",
              file=sys.stderr)  # fmt: skip
        return 1
    try:
        summary = reset(top=args.top, email=args.email)
    except ResetError as exc:
        print(f"Reset stopped: {exc}", file=sys.stderr)
        return 1
    print("Reset complete.")
    print(f"  old audit database: {summary['archived_audit'] or 'none (first run)'}")
    print(f"  cases: {summary['cases']}   notifications: {summary['notifications']}   stored briefs: {summary['stored_briefs']}")
    pre = summary["prewarm"]
    print(f"  prewarm: {pre['generated']} written, {pre['already_cached']} reused, {pre['fell_back']} used the template "
          f"({pre['llm_calls']} LLM calls, {pre['seconds']} s)")
    for o in summary["opened"]:
        print(f"  {o['case_id']}: opens OK, brief badge {o['badge']}{' (cached)' if o['cached'] and o['badge'] != 'Template' else ''}")
    print("Emails were " + ("sent for high-priority cases." if args.email else "not sent (use --email to send them)."))
    print("Next: make api   and   make web")
    return 0


if __name__ == "__main__":
    sys.exit(main())
