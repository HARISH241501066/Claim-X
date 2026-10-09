"""The demo plug-in rule in docs/demo/: it works when dropped in, and removing it restores everything."""

import importlib.util
import shutil
import sys
from datetime import date
from pathlib import Path

import pytest

from backend.data import generator as gen
from backend.detect import engine
from backend.detect.context import Context, load_tables
from backend.detect.rules import __file__ as rules_init
from backend.pipeline import run_all

DEMO_RULE = Path(__file__).resolve().parents[2] / "docs" / "demo" / "weekend_billing.py"
RULES_DIR = Path(rules_init).parent
TARGET = RULES_DIR / "weekend_billing.py"
BANNED = ("fraud", "guilty", "criminal")


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo_rule") / "t.db"
    gen.generate(path, None)
    return path


@pytest.fixture
def installed():
    """Copy the demo rule into rules/ the way the demo does, and always remove it afterwards."""
    shutil.copy(DEMO_RULE, TARGET)
    try:
        yield TARGET
    finally:
        TARGET.unlink(missing_ok=True)
        sys.modules.pop("backend.detect.rules.weekend_billing", None)
        shutil.rmtree(RULES_DIR / "__pycache__", ignore_errors=True)


def load_demo_module():
    spec = importlib.util.spec_from_file_location("weekend_billing_demo", DEMO_RULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_demo_rule_flags_two_busy_weekend_days_with_those_claims_as_evidence(db):
    tables = load_tables(db)
    findings = load_demo_module().WeekendBillingRule().evaluate(tables["claims"], Context.build(tables))
    assert [f.entity_id for f in findings] == ["PRV-019", "PRV-A01"]
    claims = tables["claims"].set_index("claim_id")
    for finding in findings:
        assert finding.severity == "low" and finding.detector == "weekend_billing"
        rows = claims.loc[finding.evidence_ids]
        assert (rows.provider_id == finding.entity_id).all() and len(rows) >= 6
        assert rows.service_date.nunique() == 1  # one day
        assert date.fromisoformat(rows.service_date.iloc[0]).weekday() >= 5  # a Saturday or Sunday
        assert "warrants review" in finding.reason and not [w for w in BANNED if w in finding.reason.lower()]


def test_the_demo_rule_is_not_in_rules_until_it_is_copied_there(db):
    assert not TARGET.exists()
    names = [r.name for r in engine.load_rules()]
    assert "weekend_billing" not in names and len(names) == 7


def test_dropping_the_file_in_adds_the_rule_to_the_next_run_without_touching_the_engine(db, installed, tmp_path):
    before = engine.run(db)
    assert "weekend_billing" in before.rules_run and len(before.rules_run) == 8
    assert before.errors == {}
    assert [f.entity_id for f in before.findings if f.detector == "weekend_billing"] == ["PRV-019", "PRV-A01"]
    installed.unlink()  # removing it puts the original seven back
    sys.modules.pop("backend.detect.rules.weekend_billing", None)
    after = engine.run(db)
    assert "weekend_billing" not in after.rules_run and len(after.rules_run) == 7


def test_the_new_rule_flows_into_the_existing_cases_and_changes_nothing_else(tmp_path, installed):
    with_rule = run_all(tmp_path / "with.db")
    installed.unlink()
    sys.modules.pop("backend.detect.rules.weekend_billing", None)
    without = run_all(tmp_path / "without.db")
    assert len(with_rule.findings) == len(without.findings) + 2
    assert [c.primary_entity for c in with_rule.cases] == [c.primary_entity for c in without.cases]  # no new case
    for provider in ("PRV-019", "PRV-A01"):  # the physiotherapy provider and the ring's referrer
        new = next(c for c in with_rule.cases if provider in c.entity_ids)
        old = next(c for c in without.cases if provider in c.entity_ids)
        assert len(new.findings) == len(old.findings) + 1
        assert any(f["detector"] == "weekend_billing" for f in new.findings)
        assert new.worst_severity == old.worst_severity  # a low-severity finding does not escalate a case
    assert with_rule.status == "ok"
