import contextlib
import logging
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

from backend.data import generator as gen
from backend.detect import engine
from backend.detect.base import Finding, Rule
from backend.detect.context import Context, load_tables
from backend.detect.rules.duplicate import DuplicateRule
from backend.detect.rules.impossible_timing import ImpossibleTimingRule
from backend.detect.rules.repeat_history import RepeatHistoryRule
from backend.detect.rules.upcoding import UpcodingRule

RULES_DIR = Path(engine.__file__).resolve().parent / "rules"
CLAIM_COLUMNS = [
    "claim_id", "member_id", "provider_id", "facility_id", "referring_provider_id",
    "service_date", "procedure_code", "code_level", "billed_amount", "claim_type",
]  # fmt: skip


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    out = tmp_path_factory.mktemp("m2")
    gen.generate(out / "t.db", out / "truth.csv")
    tables = load_tables(out / "t.db")
    ctx = Context.build(tables)
    result = engine.run_rules(tables["claims"], ctx)
    truth = pd.read_csv(out / "truth.csv")
    return {"db": out / "t.db", "tables": tables, "ctx": ctx, "result": result, "truth": truth}


def by_detector(world, name):
    return [f for f in world["result"].findings if f.detector == name]


def truth_ids(world, scenario):
    t = world["truth"]
    return set(t[t.scenario == scenario].entity_id)


def evidence(findings):
    return {e for f in findings for e in f.evidence_ids}


def make_claims(rows):
    df = pd.DataFrame(rows)
    for col in CLAIM_COLUMNS:
        if col not in df:
            df[col] = None
    return df[CLAIM_COLUMNS]


def claim(i, member="MEM-001", provider="PRV-001", facility="FAC-001", day="2026-03-02",
          code="RAD-MRI", level=None):  # fmt: skip
    return {
        "claim_id": f"CLM-T{i:04d}", "member_id": member, "provider_id": provider,
        "facility_id": facility, "service_date": day, "procedure_code": code,
        "code_level": level, "billed_amount": 1000, "claim_type": "outpatient",
    }  # fmt: skip


@contextlib.contextmanager
def temp_rule_file(name, source):
    path = RULES_DIR / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
        sys.modules.pop(f"backend.detect.rules.{name}", None)
        shutil.rmtree(RULES_DIR / "__pycache__", ignore_errors=True)


# ------------------------------------------------------------ planted scenarios


def test_all_rules_were_loaded_and_ran(world):
    assert world["result"].errors == {}
    assert world["result"].rules_run == [
        "duplicate", "impossible_timing", "phantom", "repeat_history", "unbundling", "upcoding",
        "utilization",
    ]  # fmt: skip


def test_duplicate_finds_every_planted_pair_and_nothing_else(world):
    found = by_detector(world, "duplicate")
    planted = truth_ids(world, "double_billing")
    assert len(planted) == 20 and len(found) == 20
    assert all(len(f.evidence_ids) == 2 for f in found)
    assert all(len(planted & set(f.evidence_ids)) == 1 for f in found)


def test_duplicate_does_not_fire_on_honest_same_day_followups(world):
    follow = truth_ids(world, "honest_followup")
    assert len(follow) == 40
    assert not follow & evidence(by_detector(world, "duplicate"))


def test_unbundling_finds_every_planted_claim(world):
    found = by_detector(world, "unbundling")
    assert evidence(found) == truth_ids(world, "unbundling") - {"PRV-024", "PRV-025"}
    assert len(found) == 12
    assert {f.entity_id for f in found} == {"PRV-024", "PRV-025"}


def test_phantom_finds_every_planted_claim(world):
    found = by_detector(world, "phantom")
    planted = truth_ids(world, "phantom") - {"PRV-007"}
    assert len(found) == 10
    assert planted <= evidence(found)
    stays = set(world["tables"]["inpatient_stays"].stay_id)
    assert all(f.evidence_ids[1] in stays for f in found)


def test_upcoding_flags_the_upcoder_only(world):
    found = by_detector(world, "upcoding")
    assert [f.entity_id for f in found] == ["PRV-005"]
    assert found[0].severity == "high"
    flagged = {f.entity_id for f in found}
    assert not flagged & {"PRV-015", "PRV-010"}  # honest specialist, repeat offender


def test_upcoding_without_the_guard_would_flag_the_honest_specialist(world):
    # documents why the practical-significance guard exists
    ctx = world["ctx"]
    mean, std, _ = ctx.peer_baselines["PRV-015"]
    assert (ctx.level5.loc["PRV-015"].share - mean) / std > 3


def test_utilization_flags_the_overutilizing_members(world):
    found = by_detector(world, "utilization")
    members = truth_ids(world, "overutilizer")
    assert members <= {f.entity_id for f in found}
    assert sum(f.entity_id in members for f in found) == 9  # 3 members x 3 months
    heavy = [f for f in found if f.entity_id in members]
    assert all(f.severity == "high" and len(f.evidence_ids) == 25 for f in heavy)
    # any other flag is a member the unbundling scenario also touches
    others = {f.entity_id for f in found} - members
    unbundled = set(
        world["tables"]["claims"]
        .query("claim_id in @truth", local_dict={"truth": truth_ids(world, "unbundling")})
        .member_id
    )
    assert others <= unbundled


def test_impossible_timing_is_quiet_on_generated_data(world):
    # on generated data it may only fire where a planted phantom claim is involved
    phantom = truth_ids(world, "phantom")
    assert all(set(f.evidence_ids) & phantom for f in by_detector(world, "impossible_timing"))


# ------------------------------------------------------------ rule behaviour on fixtures


def fixture_ctx(world, claims):
    return Context.build({**world["tables"], "claims": claims})


def test_impossible_timing_flags_member_in_two_distant_cities(world):
    claims = make_claims(
        [claim(1, facility="FAC-001"), claim(2, facility="FAC-003")]  # Chennai and Delhi
    )
    out = ImpossibleTimingRule().evaluate(claims, fixture_ctx(world, claims))
    assert len(out) == 1 and out[0].entity_id == "MEM-001"
    assert set(out[0].evidence_ids) == {"CLM-T0001", "CLM-T0002"}


def test_impossible_timing_ignores_nearby_facilities_and_other_days(world):
    claims = make_claims(
        [
            claim(1, facility="FAC-001"),
            claim(2, facility="FAC-C01"),  # same city
            claim(3, facility="FAC-003", day="2026-03-09"),  # distant but another day
        ]
    )
    assert ImpossibleTimingRule().evaluate(claims, fixture_ctx(world, claims)) == []


def test_impossible_timing_flags_provider_over_24_hours(world):
    over = make_claims([claim(i, member=f"MEM-{i:03d}") for i in range(1, 26)])  # 25 x 60 min
    out = ImpossibleTimingRule().evaluate(over, fixture_ctx(world, over))
    assert [f.entity_id for f in out] == ["PRV-001"] and out[0].severity == "high"
    ok = make_claims([claim(i, member=f"MEM-{i:03d}") for i in range(1, 24)])  # 23 hours
    assert ImpossibleTimingRule().evaluate(ok, fixture_ctx(world, ok)) == []


def test_duplicate_differing_code_is_not_a_duplicate(world):
    rows = [
        claim(1, code="CON-GM-3", level=3),
        claim(2, code="CON-GM-1", level=1),  # same-day follow-up
    ]
    claims = make_claims(rows)
    assert DuplicateRule().evaluate(claims, fixture_ctx(world, claims)) == []


def test_upcoding_reports_insufficient_data_instead_of_guessing(world, caplog):
    gm = world["tables"]["claims"].query("provider_id in ['PRV-005','PRV-001','PRV-002']")
    ctx = fixture_ctx(world, gm)  # only two peers per provider
    with caplog.at_level(logging.INFO, logger="claimshield.detect.upcoding"):
        assert UpcodingRule().evaluate(gm, ctx) == []
    assert "Insufficient data" in caplog.text


# ------------------------------------------------------------ evidence, wording, validation


def test_every_finding_links_to_existing_evidence_and_avoids_accusatory_words(world):
    tables = world["tables"]
    known = (
        set(tables["claims"].claim_id)
        | set(tables["inpatient_stays"].stay_id)
        | set(tables["investigations"].case_id)
    )
    assert world["result"].findings
    for f in world["result"].findings:
        assert f.evidence_ids and set(f.evidence_ids) <= known
        text = f.reason.lower()
        assert "fraud" not in text
        assert "warrants review" in text
        assert 0 <= f.score <= 1 and f.severity in ("low", "medium", "high")


def test_finding_requires_evidence_and_valid_fields():
    ok = {"entity_id": "PRV-1", "detector": "d", "score": 0.5, "severity": "low", "reason": "r"}
    with pytest.raises(ValueError):
        Finding(**ok, evidence_ids=[])
    with pytest.raises(ValueError):
        Finding(**{**ok, "score": 1.5}, evidence_ids=["CLM-1"])
    with pytest.raises(ValueError):
        Finding(**{**ok, "severity": "critical"}, evidence_ids=["CLM-1"])
    assert set(Finding(**ok, evidence_ids=["CLM-1"]).to_dict()) == {
        "entity_id", "detector", "score", "severity", "reason", "evidence_ids",
    }  # fmt: skip


# ------------------------------------------------------------ engine behaviour

DUMMY = '''
from backend.detect.base import Finding, Rule


class ZzDummyRule(Rule):
    name = "zz_dummy"

    def evaluate(self, claims, ctx):
        return [Finding(entity_id="PRV-DUMMY", detector=self.name, score=0.1, severity="low",
                        reason="dummy finding that warrants review",
                        evidence_ids=[claims.claim_id.iloc[0]])]
'''


def test_dummy_rule_file_is_auto_loaded_with_no_engine_change(world):
    assert "zz_dummy" not in [r.name for r in engine.load_rules()]
    with temp_rule_file("zz_dummy_rule", DUMMY):
        rules = engine.load_rules()
        assert "zz_dummy" in [r.name for r in rules]
        result = engine.run_rules(world["tables"]["claims"], world["ctx"])
        assert any(f.detector == "zz_dummy" for f in result.findings)
        assert len(result.rules_run) == 8
    assert "zz_dummy" not in [r.name for r in engine.load_rules()]


class BoomRule(Rule):
    name = "boom"

    def evaluate(self, claims, ctx):
        raise RuntimeError("kaboom")


class JunkRule(Rule):
    name = "junk"

    def evaluate(self, claims, ctx):
        return ["not a finding"]


def test_a_rule_that_raises_is_skipped_and_logged(world, caplog):
    rules = [BoomRule(), JunkRule(), DuplicateRule()]
    with caplog.at_level(logging.ERROR, logger="claimshield.detect"):
        result = engine.run_rules(world["tables"]["claims"], world["ctx"], rules)
    assert set(result.errors) == {"boom", "junk"}
    assert "kaboom" in result.errors["boom"]
    assert "rule boom failed and was skipped" in caplog.text
    assert result.rules_run == ["duplicate"]
    assert len(result.findings) == 20  # the healthy rule still ran


def test_a_rule_module_that_fails_to_import_is_skipped_and_logged(caplog):
    broken = temp_rule_file("zz_broken_rule", "raise RuntimeError('bad module')\n")
    with broken, caplog.at_level(logging.ERROR, logger="claimshield.detect"):
        names = [r.name for r in engine.load_rules()]
    assert "duplicate" in names
    assert "zz_broken_rule: import failed" in caplog.text


def test_findings_table_roundtrip_and_run_is_deterministic(world, tmp_path):
    first = tmp_path / "a.db"
    shutil.copy(world["db"], first)
    result = engine.run(first)
    saved = engine.load_findings(first)
    assert len(saved) == len(result.findings) > 0
    assert saved[0]["finding_id"] == "FND-000001"
    assert all(isinstance(r["evidence_ids"], list) and r["evidence_ids"] for r in saved)
    second = tmp_path / "b.db"
    shutil.copy(world["db"], second)
    engine.run(second)
    assert engine.load_findings(second) == saved
    engine.run(first)  # re-running replaces the table instead of appending
    assert len(engine.load_findings(first)) == len(saved)


# ------------------------------------------------------------ repeat_history


def test_repeat_history_flags_the_repeat_offender_only(world):
    found = by_detector(world, "repeat_history")
    assert {f.entity_id for f in found} == truth_ids(world, "repeat_offender") == {"PRV-010"}
    f = found[0]
    assert f.evidence_ids[0].startswith("CASE-") and f.evidence_ids[1].startswith("CLM-")
    assert "2.4×" in f.reason and "confirmed investigation" in f.reason
    assert "PRV-015" not in {x.entity_id for x in found}  # cleared case: honest specialist


def history_fixture(world, recent_per_month, outcome="confirmed", closed="2025-09-15"):
    rows = []
    for month in range(1, 7):
        n = 10 if month <= 4 else recent_per_month
        rows += [
            claim(len(rows) + 1, member=f"MEM-{i:03d}", day=f"2026-{month:02d}-{(i % 27) + 1:02d}")
            for i in range(n)
        ]
    claims = make_claims(rows)
    inv = pd.DataFrame(
        [{"case_id": "CASE-900", "entity_id": "PRV-001", "entity_type": "provider",
          "opened_date": "2025-06-01", "closed_date": closed, "outcome": outcome}]
    )  # fmt: skip
    ctx = Context.build({**world["tables"], "claims": claims, "investigations": inv})
    return claims, ctx


def test_repeat_history_needs_a_confirmed_case_and_a_real_rise(world):
    rule = RepeatHistoryRule()
    claims, ctx = history_fixture(world, recent_per_month=20)  # 2.0x
    assert [f.entity_id for f in rule.evaluate(claims, ctx)] == ["PRV-001"]
    claims, ctx = history_fixture(world, recent_per_month=14)  # 1.4x, below the threshold
    assert rule.evaluate(claims, ctx) == []
    claims, ctx = history_fixture(world, recent_per_month=20, outcome="cleared")
    assert rule.evaluate(claims, ctx) == []
    claims, ctx = history_fixture(world, recent_per_month=20, closed="2026-05-20")  # not "past"
    assert rule.evaluate(claims, ctx) == []
    claims, ctx = history_fixture(world, recent_per_month=5)  # volume fell
    assert rule.evaluate(claims, ctx) == []
