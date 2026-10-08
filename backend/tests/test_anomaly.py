import json
import logging
import sqlite3

import numpy as np
import pandas as pd
import pytest

from backend.data import generator as gen
from backend.detect import anomaly, engine
from backend.features.provider_features import FEATURES


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    out = tmp_path_factory.mktemp("m3")
    db = out / "t.db"
    gen.generate(db, out / "truth.csv")
    rules = engine.run(db)
    result = anomaly.run(db)
    con = sqlite3.connect(db)
    claims = pd.read_sql_query("SELECT claim_id, provider_id FROM claims", con)
    con.close()
    return {"db": db, "rules": rules, "result": result, "claims": claims}


def rank_of(pipeline, provider_id):
    return int(pipeline["result"].scores.loc[provider_id, "score_rank"])


# ------------------------------------------------------------ the gate


def test_planted_providers_are_in_the_top_10(pipeline):
    for provider_id in ("PRV-005", "PRV-019", "PRV-A01"):  # upcoder, over-utilizer, ring
        assert rank_of(pipeline, provider_id) <= 10, provider_id


def test_busy_honest_specialist_is_not_in_the_top_5(pipeline):
    assert rank_of(pipeline, "PRV-015") > 5


# ------------------------------------------------------------ scores and findings


def test_scores_are_rescaled_to_0_1_and_ranks_are_complete(pipeline):
    scores = pipeline["result"].scores
    assert len(scores) == 40
    assert scores.anomaly_score.between(0, 1).all()
    assert scores.anomaly_score.max() == 1 and scores.anomaly_score.min() == 0
    assert list(scores.score_rank) == list(range(1, 41))
    assert scores.anomaly_score.is_monotonic_decreasing


def test_findings_are_emitted_only_above_the_95th_percentile(pipeline):
    result = pipeline["result"]
    scores = result.scores.anomaly_score
    above = scores[scores > np.percentile(scores, 95)]
    assert len(above) == 2
    assert {f.entity_id for f in result.findings} == set(above.index)
    assert all(f.detector == "anomaly" for f in result.findings)
    flagged = set(result.scores.index[result.scores.flagged == 1])
    assert flagged == set(above.index)


def test_findings_have_drivers_evidence_and_careful_wording(pipeline):
    claims = pipeline["claims"]
    assert pipeline["result"].findings
    for f in pipeline["result"].findings:
        assert f.reason.count("× peers") <= anomaly.TOP_DRIVERS
        assert "× peers" in f.reason and "warrants review" in f.reason
        assert "fraud" not in f.reason.lower()
        assert f.evidence_ids == sorted(claims[claims.provider_id == f.entity_id].claim_id)
        assert 0 < f.score <= 1 and f.severity in ("low", "medium", "high")


def test_model_is_deterministic(pipeline, tmp_path):
    db = tmp_path / "again.db"
    gen.generate(db, tmp_path / "t.csv")
    engine.run(db)
    again = anomaly.run(db)
    first = pipeline["result"].scores
    pd.testing.assert_frame_equal(first.drop(columns="drivers"), again.scores.drop(columns="drivers"))
    assert first.drivers.tolist() == again.scores.drivers.tolist()


# ------------------------------------------------------------ persistence


def test_anomaly_findings_are_appended_without_losing_rule_findings(pipeline):
    con = sqlite3.connect(pipeline["db"])
    try:
        rows = con.execute("SELECT finding_id, detector FROM findings").fetchall()
        stored = con.execute("SELECT COUNT(*) FROM provider_anomaly_scores").fetchone()[0]
    finally:
        con.close()
    assert sum(d != "anomaly" for _, d in rows) == len(pipeline["rules"].findings)
    assert sum(d == "anomaly" for _, d in rows) == len(pipeline["result"].findings) == 2
    assert len({i for i, _ in rows}) == len(rows)  # finding IDs stay unique
    assert stored == 40


def test_rerunning_anomaly_replaces_rows_instead_of_duplicating(pipeline, tmp_path):
    import shutil

    db = tmp_path / "rerun.db"
    shutil.copy(pipeline["db"], db)
    anomaly.run(db)
    anomaly.run(db)
    con = sqlite3.connect(db)
    try:
        counts = dict(con.execute("SELECT detector, COUNT(*) FROM findings GROUP BY 1").fetchall())
    finally:
        con.close()
    assert counts["anomaly"] == 2
    assert sum(counts.values()) - 2 == len(pipeline["rules"].findings)


def test_drivers_are_stored_for_every_provider(pipeline):
    con = sqlite3.connect(pipeline["db"])
    try:
        raw = con.execute(
            "SELECT drivers FROM provider_anomaly_scores WHERE provider_id='PRV-A01'"
        ).fetchone()[0]
    finally:
        con.close()
    parsed = json.loads(raw)
    assert 1 <= len(parsed) <= 3
    assert {"feature", "value", "peer_median", "deviation"} <= set(parsed[0])


# ------------------------------------------------------------ driver maths and safety


def synthetic_features(target_values):
    """Five peers with a known median/MAD plus one target provider."""
    peer_services = [0.8, 0.9, 1.0, 1.1, 1.2]  # median 1.0, MAD 0.1
    rows = {f"P{i}": {f: 1.0 for f in FEATURES} for i in range(5)}
    for i, v in enumerate(peer_services):
        rows[f"P{i}"]["services_per_member_month"] = v
    rows["T"] = {f: 1.0 for f in FEATURES} | target_values
    df = pd.DataFrame.from_dict(rows, orient="index")
    df["specialty"] = "General Medicine"
    return df


def test_driver_text_reports_multiple_of_peer_median():
    df = synthetic_features({"services_per_member_month": 6.2})
    found, scope = anomaly.drivers(df, "T")
    assert scope == "General Medicine peers"
    assert [d["feature"] for d in found] == ["services_per_member_month"]
    assert anomaly.driver_text(found[0]) == "Services per member 6.2× peers (6.20 vs 1.00)"


def test_only_top_three_positive_deviations_are_used_and_sorted():
    df = synthetic_features(
        {
            "services_per_member_month": 6.2,
            "claims_per_day": 4.0,
            "avg_amount_ratio": 3.0,
            "weekend_ratio": 2.0,
            "referred_claim_share": 0.1,  # below peers: never a driver
        }
    )
    found, _ = anomaly.drivers(df, "T")
    assert len(found) == 3
    deviations = [d["deviation"] for d in found]
    assert deviations == sorted(deviations, reverse=True) and all(d > 0 for d in deviations)


def test_small_specialty_falls_back_to_all_providers():
    df = synthetic_features({"services_per_member_month": 6.2})
    df.loc[df.index[:4], "specialty"] = "Radiology"  # leaves only 2 General Medicine peers
    _, scope = anomaly.drivers(df, "T")
    assert scope == "all providers"


def test_too_little_data_produces_no_scores_and_logs(caplog):
    tiny = synthetic_features({}).iloc[:5]
    with caplog.at_level(logging.WARNING, logger="claimshield.anomaly"):
        result = anomaly.analyze(tiny, pd.DataFrame({"claim_id": ["C"], "provider_id": ["P0"]}))
    assert result.scores.empty and result.findings == []
    assert "Insufficient data" in caplog.text


def test_missing_feature_values_produce_no_scores(caplog):
    df = pd.concat([synthetic_features({})] * 3)
    df.index = [f"P{i}" for i in range(len(df))]
    df.iloc[0, df.columns.get_loc("claims_per_day")] = np.nan
    with caplog.at_level(logging.WARNING, logger="claimshield.anomaly"):
        assert anomaly.score_providers(df) is None
    assert "Insufficient data" in caplog.text
