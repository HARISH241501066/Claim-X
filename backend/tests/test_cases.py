import json
import logging
import random
import re
import shutil
import sqlite3

import pandas as pd
import pytest

from backend.cases import builder, ranking
from backend.cases.builder import STATUS_AWAITING, Case
from backend.cases.ranking import Weights
from backend.data import generator as gen
from backend.detect import anomaly, engine, graph

RING = ["PRV-A01", "FAC-B01", "FAC-C01", "OWN-001", "OWN-002"]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    out = tmp_path_factory.mktemp("m5")
    db = out / "t.db"
    gen.generate(db, out / "truth.csv")
    engine.run(db)
    anomaly.run(db)
    graph.run(db)
    result = ranking.run(db)
    truth = pd.read_csv(out / "truth.csv")
    return {"db": db, "result": result, "truth": truth}


def truth_ids(world, scenario):
    t = world["truth"]
    return set(t[t.scenario == scenario].entity_id)


def case_for(world, entity):
    return next(c for c in world["result"].cases if entity in c.entity_ids)


def rank_of(world, case_id):
    ranked = world["result"].ranked
    return int(ranked[ranked.case_id == case_id]["rank"].iloc[0])


def make_case(i, kind="provider", rule=0.0, anomaly_s=0.0, graph_s=0.0, amount=0, members=0,
              severity="medium", fired=("rules",), entities=None):  # fmt: skip
    return Case(
        case_id=f"CASE-{i:04d}", case_type=kind, primary_entity=f"E{i}",
        entity_ids=entities or [f"E{i}"], flagged_amount=amount,
        affected_members=[f"M{j}" for j in range(members)], detectors_fired=list(fired),
        rule_score=rule, anomaly_score=anomaly_s, graph_score=graph_s, worst_severity=severity,
    )  # fmt: skip


# ------------------------------------------------------------ the gate


def test_the_ring_is_case_number_one(world):
    top = world["result"].ranked.iloc[0]
    case = next(c for c in world["result"].cases if c.case_id == top.case_id)
    assert top["rank"] == 1 and case.case_type == "ring"
    assert set(RING) <= set(case.entity_ids)
    assert case.detectors_fired == ["anomaly", "graph"]
    margin = top.priority - world["result"].ranked.iloc[1].priority
    assert margin > 0.1  # a clear lead, not a photo finish


def test_the_honest_busy_specialist_is_not_in_the_top_5_and_has_no_case(world):
    assert not any("PRV-015" in c.entity_ids for c in world["result"].cases)
    top5 = set(world["result"].ranked.head(5).case_id)
    assert all("PRV-015" not in c.entity_ids for c in world["result"].cases if c.case_id in top5)
    assert not any(f["entity_id"] == "PRV-015" for c in world["result"].cases for f in c.findings)


def test_every_planted_scenario_has_a_case(world):
    cases = world["result"].cases
    entities = {e for c in cases for e in c.entity_ids}
    flagged_claims = {x for c in cases for x in c.flagged_claim_ids}
    members = {m for c in cases for m in c.affected_members}
    assert set(RING) <= set(case_for(world, "PRV-A01").entity_ids)  # ring: one case, all five
    assert truth_ids(world, "upcoder") <= entities
    assert truth_ids(world, "unbundling") - flagged_claims <= entities  # the two providers
    assert truth_ids(world, "phantom") - flagged_claims <= entities
    assert truth_ids(world, "repeat_offender") <= entities
    assert truth_ids(world, "overutilizer") <= members
    assert truth_ids(world, "double_billing") <= flagged_claims
    assert {"PRV-005", "PRV-007", "PRV-010", "PRV-019", "PRV-024", "PRV-025"} <= entities


def test_planted_claims_are_inside_the_right_cases(world):
    unbundled = truth_ids(world, "unbundling") - {"PRV-024", "PRV-025"}
    assert unbundled <= set(case_for(world, "PRV-024").flagged_claim_ids) | set(
        case_for(world, "PRV-025").flagged_claim_ids
    )
    heavy = case_for(world, "PRV-019")
    assert truth_ids(world, "overutilizer") <= set(heavy.affected_members)
    assert heavy.detectors_fired == ["rules", "anomaly"]


def test_honest_followups_are_not_flagged_in_any_case(world):
    followups = truth_ids(world, "honest_followup")
    assert len(followups) == 40
    assert not followups & {x for c in world["result"].cases for x in c.flagged_claim_ids}


def test_same_weights_always_give_the_same_order(world):
    cases = world["result"].cases
    expected = world["result"].ranked.case_id.tolist()
    for seed in range(5):
        shuffled = cases[:]
        random.Random(seed).shuffle(shuffled)
        assert ranking.rank_cases(shuffled).case_id.tolist() == expected
    again = ranking.run(world["db"]).ranked.case_id.tolist()
    assert again == expected


# ------------------------------------------------------------ weights and factor maths


def test_weights_must_be_valid():
    assert Weights().risk == 0.30
    with pytest.raises(ValueError):
        Weights(0.5, 0.5, 0.5, 0.0, 0.0)  # sums to 1.5
    with pytest.raises(ValueError):
        Weights(-0.1, 0.6, 0.2, 0.2, 0.1)  # negative


def test_different_weights_can_change_the_order_but_not_case_ids(world):
    impact_only = ranking.rank_cases(world["result"].cases, Weights(0, 0, 1, 0, 0))
    default = world["result"].ranked
    assert impact_only.iloc[0].case_id != default.iloc[0].case_id
    top = next(c for c in world["result"].cases if c.case_id == impact_only.iloc[0].case_id)
    assert len(top.affected_members) == max(len(c.affected_members) for c in world["result"].cases)
    assert set(impact_only.case_id) == set(default.case_id)  # IDs are stable under any weights


def test_priority_matches_a_hand_computation():
    a = make_case(1, rule=0.9, anomaly_s=0.6, graph_s=0.0, amount=100, members=4, severity="high",
                  fired=("rules", "anomaly"))  # fmt: skip
    b = make_case(2, rule=0.3, amount=50, members=8, severity="medium", fired=("rules",))
    ranked = ranking.rank_cases([a, b]).set_index("case_id")
    # risk: a=(0.9+0.6)/3=0.5, b=0.1 -> scaled by max: 1.0 and 0.2; dollars 1.0/0.5; impact 0.5/1.0
    expected_a = 0.30 * 1.0 + 0.25 * 1.0 + 0.15 * 0.5 + 0.15 * 0.75 + 0.15 * (2 / 3)
    expected_b = 0.30 * 0.2 + 0.25 * 0.5 + 0.15 * 1.0 + 0.15 * 0.5 + 0.15 * (1 / 3)
    assert ranked.loc["CASE-0001", "priority"] == pytest.approx(expected_a, abs=1e-6)
    assert ranked.loc["CASE-0002", "priority"] == pytest.approx(expected_b, abs=1e-6)
    assert ranked.loc["CASE-0001", "rank"] == 1


def test_severity_scores_and_zero_cases_are_handled():
    assert ranking.SEVERITY_SCORE == {"critical": 1.0, "high": 0.75, "medium": 0.5, "low": 0.25}
    flat = ranking.rank_cases([make_case(1), make_case(2)])  # no dollars, members or risk at all
    assert (flat.dollars == 0).all() and (flat.impact == 0).all() and (flat.risk == 0).all()
    assert flat.case_id.tolist() == ["CASE-0001", "CASE-0002"]  # ties fall back to case_id
    assert ranking.rank_cases([]).empty


# ------------------------------------------------------------ capacity


def test_effort_hours_for_providers_and_rings():
    assert ranking.effort_hours(make_case(1)) == 4.0
    ring = make_case(2, kind="ring", entities=RING)
    assert ranking.effort_hours(ring) == 4.0 + 5  # 4 h + 1 h per entity


def test_schedule_follows_priority_until_hours_run_out():
    cases = [
        make_case(1, kind="ring", rule=1, amount=900, members=9, entities=RING),
        make_case(2, rule=0.8, amount=500, members=5),
        make_case(3, rule=0.6, amount=400, members=4),
        make_case(4, rule=0.4, amount=300, members=3),
    ]
    ranked = ranking.schedule(ranking.rank_cases(cases), cases, team_hours=14)
    queue = dict(zip(ranked.case_id, ranked.queue, strict=True))
    assert queue == {"CASE-0001": "scheduled", "CASE-0002": "scheduled",
                     "CASE-0003": "backlog", "CASE-0004": "backlog"}  # fmt: skip
    assert ranked.cumulative_hours.tolist() == [9.0, 13.0, 13.0, 13.0]
    assert ranking.schedule(ranking.rank_cases(cases), cases, team_hours=0).queue.eq("backlog").all()


def test_a_big_case_blocks_cheaper_ones_below_it():
    cases = [make_case(1, rule=1, amount=900, members=9), make_case(2, kind="ring", rule=0.5,
             amount=500, members=5, entities=RING), make_case(3, rule=0.1, amount=10, members=1)]  # fmt: skip
    ranked = ranking.schedule(ranking.rank_cases(cases), cases, team_hours=10)
    order = dict(zip(ranked.case_id, ranked.queue, strict=True))
    # case 1 (4 h) fits; the ring (9 h) does not; case 3 (4 h) would fit but must not jump it
    assert order == {"CASE-0001": "scheduled", "CASE-0002": "backlog", "CASE-0003": "backlog"}


def test_real_capacity_split(world):
    res = world["result"]
    scheduled = res.ranked[res.ranked.queue == "scheduled"]
    assert res.scheduled_hours == scheduled.effort_hours.sum() <= res.team_hours == 40.0
    assert scheduled["rank"].max() < res.ranked[res.ranked.queue == "backlog"]["rank"].min()
    ring = res.ranked[res.ranked.case_id == case_for(world, "PRV-A01").case_id].iloc[0]
    assert ring.effort_hours == 9.0 and ring.queue == "scheduled"
    assert (res.ranked.effort_hours.isin([4.0, 9.0])).all()


# ------------------------------------------------------------ case content and persistence


def test_every_case_awaits_human_review_and_links_to_findings(world):
    con = sqlite3.connect(world["db"])
    try:
        rows = con.execute("SELECT * FROM cases").fetchall()
        known = {r[0] for r in con.execute("SELECT finding_id FROM findings")}
        statuses = {r[0] for r in con.execute("SELECT status FROM cases")}
        cols = [d[0] for d in con.execute("SELECT * FROM cases").description]
    finally:
        con.close()
    assert statuses == {STATUS_AWAITING} == {"Awaiting human review"}
    assert len(rows) == len(world["result"].cases) == 20
    for row in (dict(zip(cols, r, strict=True)) for r in rows):
        assert re.fullmatch(r"CASE-\d{4}", row["case_id"])  # distinct from M1's CASE-### history
        assert set(json.loads(row["finding_ids"])) <= known and json.loads(row["finding_ids"])
        assert row["summary"] and "fraud" not in row["summary"].lower()
        assert row["queue"] in ("scheduled", "backlog")
        assert row["n_members"] == len(json.loads(row["affected_members"]))
    assert {c.status for c in world["result"].cases} == {STATUS_AWAITING}


def test_flagged_amount_comes_from_claim_level_evidence_only(world):
    ring = case_for(world, "PRV-A01")
    con = sqlite3.connect(world["db"])
    try:
        marks = ",".join("?" * len(ring.flagged_claim_ids))
        total = con.execute(
            f"SELECT SUM(billed_amount) FROM claims WHERE claim_id IN ({marks})",
            ring.flagged_claim_ids,
        ).fetchone()[0]
        own_claims = con.execute(
            "SELECT COUNT(*) FROM claims WHERE provider_id = 'PRV-A01'"
        ).fetchone()[0]
    finally:
        con.close()
    assert ring.flagged_amount == total
    assert len(ring.flagged_claim_ids) == 180 < own_claims == 270  # anomaly evidence excluded
    assert len(ring.affected_members) == 15


def test_member_level_findings_attach_to_the_billing_provider(world):
    heavy = case_for(world, "PRV-019")
    detectors = {f["detector"] for f in heavy.findings}
    assert {"utilization", "anomaly"} <= detectors
    assert any(f["entity_id"].startswith("MEM-") for f in heavy.findings)


def test_case_ids_are_stable_and_unique(world):
    ids = [c.case_id for c in world["result"].cases]
    assert ids == [f"CASE-{i:04d}" for i in range(1, len(ids) + 1)]
    assert world["result"].cases[0].case_type == "ring"  # rings come first in build order
    rebuilt = builder.build_cases(world["db"])
    assert [(c.case_id, c.primary_entity) for c in rebuilt] == [
        (c.case_id, c.primary_entity) for c in world["result"].cases
    ]


def test_rerunning_replaces_the_cases_table(world, tmp_path):
    db = tmp_path / "again.db"
    shutil.copy(world["db"], db)
    ranking.run(db)
    ranking.run(db, Weights(0.2, 0.2, 0.2, 0.2, 0.2), team_hours=100)
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 20
        assert con.execute("SELECT COUNT(*) FROM cases WHERE queue='backlog'").fetchone()[0] == 0
    finally:
        con.close()


def test_missing_findings_produce_no_cases_and_a_log(tmp_path, caplog):
    db = tmp_path / "bare.db"
    gen.generate(db, tmp_path / "t.csv")  # no rules, anomaly or graph run
    with caplog.at_level(logging.WARNING, logger="claimshield.cases"):
        assert builder.build_cases(db) == []
        assert ranking.run(db).ranked.empty
    assert "Insufficient data" in caplog.text
