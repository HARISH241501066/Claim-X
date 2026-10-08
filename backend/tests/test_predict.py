import json
import logging
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from backend.cases import builder, ranking
from backend.cases.builder import Case
from backend.data import generator as gen
from backend.detect import anomaly, engine, graph
from backend.predict import dataset as ds
from backend.predict import model as md

REPO = Path(__file__).resolve().parents[2]
BANNED = "fraud" + "_prob"  # built in two pieces so this file never contains the string


# ------------------------------------------------------------ fixtures


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    out = tmp_path_factory.mktemp("m6")
    db = out / "t.db"
    gen.generate(db, out / "truth.csv")
    engine.run(db)
    anomaly.run(db)
    graph.run(db)
    result = md.run(db, out / "metrics.json")
    return {"db": db, "dir": out, "result": result, "metrics_path": out / "metrics.json"}


def claim(cid, member, provider, day, amount, level, referring=None):
    return {
        "claim_id": cid, "member_id": member, "provider_id": provider, "service_date": day,
        "billed_amount": amount, "code_level": level, "referring_provider_id": referring,
    }  # fmt: skip


CLAIMS = pd.DataFrame(
    [
        claim("C1", "M1", "P1", "2026-01-10", 1000, 5),  # Saturday
        claim("C2", "M1", "P1", "2026-02-10", 3000, 3, "PRV-X"),
        claim("C3", "M2", "P1", "2026-02-20", 2000, None),
        claim("C4", "M2", "P1", "2026-03-05", 9999, 5),  # after the cutoff: must be ignored
        claim("C5", "M3", "P2", "2026-02-27", 500, 1),
    ]
)
PROVIDERS = pd.DataFrame({"provider_id": ["P1", "P2"], "specialty": ["General Medicine"] * 2})


def investigation(entity, opened, closed, outcome, etype="provider"):
    return {"entity_id": entity, "entity_type": etype, "opened_date": opened,
            "closed_date": closed, "outcome": outcome}  # fmt: skip


INVESTIGATIONS = pd.DataFrame(
    [
        investigation("P1", "2025-12-01", "2026-02-15", "confirmed"),  # counts at the cutoff
        investigation("P1", "2026-02-01", "2026-03-10", "confirmed"),  # closes after the cutoff
        investigation("P1", "2025-11-01", "2025-12-20", "cleared"),  # cleared never counts
        investigation("F1", "2025-11-01", "2025-12-20", "confirmed", "facility"),  # not a provider
    ]
)
CUTOFF = date(2026, 2, 28)


# ------------------------------------------------------------ dataset


def test_cutoffs_are_the_month_ends_of_months_2_to_5():
    assert ds.CUTOFFS == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31)]
    assert ds.HORIZONS == (30, 60, 90)


def test_features_match_hand_computed_values():
    f = ds.features_at(CLAIMS, PROVIDERS, INVESTIGATIONS, CUTOFF)
    p1, p2 = f.loc["P1"], f.loc["P2"]
    # window = 59 days; specialty median amount of the 4 claims seen = (1000 + 2000) / 2 = 1500
    assert p1.claims_per_day == pytest.approx(3 / 59)
    assert p1.avg_amount_ratio == pytest.approx(2000 / 1500)
    assert p1.level5_share == pytest.approx(0.5)  # C1 level 5, C2 level 3, C3 has no level
    assert p1.services_per_member_month == pytest.approx(1.0)  # (M1,Jan) (M1,Feb) (M2,Feb)
    assert p1.repeat_member_ratio == pytest.approx(1 / 3)
    assert p1.referred_claim_share == pytest.approx(1 / 3)
    assert p1.weekend_ratio == pytest.approx(1 / 3)  # only C1 (a Saturday)
    assert p1.trend == pytest.approx((2 + 1) / (1 + 1))  # 2 in the last 30 days, 1 before
    assert p1.prior_confirmed_count == 1
    assert p1.history_days == 50 and p1.low_confidence
    assert p2.trend == pytest.approx((1 + 1) / (0 + 1)) and p2.prior_confirmed_count == 0
    assert p2.avg_amount_ratio == pytest.approx(500 / 1500) and p2.level5_share == 0


def test_features_never_use_data_after_the_cutoff():
    before = ds.features_at(CLAIMS, PROVIDERS, INVESTIGATIONS, CUTOFF)
    later = pd.concat([CLAIMS, pd.DataFrame([claim("C9", "M9", "P1", "2026-03-20", 50, 5)])])
    after = ds.features_at(later, PROVIDERS, INVESTIGATIONS, CUTOFF)
    pd.testing.assert_frame_equal(before, after)
    assert ds.features_at(CLAIMS[CLAIMS.service_date < "2026-01-01"], PROVIDERS, INVESTIGATIONS, CUTOFF).empty


def test_labels_are_confirmed_provider_investigations_opened_after_the_cutoff():
    inv = pd.DataFrame(
        [
            investigation("A", "2026-03-01", "2026-04-01", "confirmed"),  # day after: counts
            investigation("B", "2026-02-28", "2026-04-01", "confirmed"),  # on the cutoff: no
            investigation("C", "2026-03-30", "2026-05-01", "confirmed"),  # day 30: counts
            investigation("D", "2026-04-15", "2026-05-20", "confirmed"),  # day 46: 60/90 only
            investigation("E", "2026-03-05", "2026-04-01", "cleared"),  # cleared: no
            investigation("F", "2026-03-05", "2026-04-01", "confirmed", "facility"),  # facility
        ]
    )
    ids = pd.Index(list("ABCDEF") + ["G"])
    out = ds.label_columns(ids, inv, CUTOFF)
    assert out.label_30.to_dict() == {"A": 1, "B": 0, "C": 1, "D": 0, "E": 0, "F": 0, "G": 0}
    assert out.loc["D", ["label_60", "label_90"]].tolist() == [1, 1]
    assert not out.censored_30.any() and not out.censored_60.any() and not out.censored_90.any()


def test_windows_past_the_end_of_the_data_are_censored():
    ids = pd.Index(["A"])
    last = ds.label_columns(ids, INVESTIGATIONS, date(2026, 5, 31)).iloc[0]
    assert not last.censored_30 and last.censored_60 and last.censored_90
    april = ds.label_columns(ids, INVESTIGATIONS, date(2026, 4, 30)).iloc[0]
    assert not april.censored_60 and april.censored_90


def test_dataset_has_a_row_per_provider_and_cutoff(pipeline):
    data = ds.build_dataset(pipeline["db"])
    assert len(data) == 4 * 40 and set(data.cutoff) == {c.isoformat() for c in ds.CUTOFFS}
    assert set(ds.FEATURES) <= set(data.columns) and not data[ds.FEATURES].isna().any().any()
    assert data.groupby("cutoff").label_30.sum().min() >= 2  # every window has positives
    first = data[data.cutoff == "2026-02-28"]
    assert first.low_confidence.all()  # under 60 days of history at the first cutoff
    assert not data[data.cutoff == "2026-03-31"].low_confidence.any()


# ------------------------------------------------------------ bands and policy


def test_band_boundaries():
    assert [md.band_for(x) for x in (0.0, 0.299, 0.3, 0.599, 0.6, 1.0)] == [
        "Low", "Low", "Medium", "Medium", "High", "High",
    ]  # fmt: skip


def test_history_rule_raises_the_band_but_never_the_number():
    band, source, reason = md.apply_band_policy(0.03, prior_confirmed_count=1, trend=1.7)
    assert (band, source) == ("High", "escalated")
    assert "history rule" in reason and "model alone: Low" in reason
    assert md.apply_band_policy(0.03, 0, 1.7)[:2] == ("Low", "model")  # no prior case
    assert md.apply_band_policy(0.03, 1, 1.4)[:2] == ("Low", "model")  # trend too small
    assert md.apply_band_policy(0.4, 1, 1.5)[:2] == ("High", "escalated")  # exactly 1.5
    assert md.apply_band_policy(0.8, 1, 2.0)[:2] == ("High", "model")  # already High


# ------------------------------------------------------------ model and metrics


def test_time_split_trains_on_earlier_cutoffs_and_tests_on_the_last(pipeline):
    data = ds.build_dataset(pipeline["db"])
    train, test = md.split_by_time(data, 30)
    assert sorted(train.cutoff.unique()) == ["2026-02-28", "2026-03-31", "2026-04-30"]
    assert list(test.cutoff.unique()) == ["2026-05-31"]
    train60, test60 = md.split_by_time(data, 60)  # 05-31 + 60 days is past the data
    assert list(test60.cutoff.unique()) == ["2026-04-30"] and "2026-05-31" not in set(train60.cutoff)
    assert list(md.split_by_time(data, 90)[1].cutoff.unique()) == ["2026-03-31"]


def test_metrics_json_has_all_four_metrics(pipeline):
    metrics = json.loads(pipeline["metrics_path"].read_text(encoding="utf-8"))
    assert metrics["target"] == "investigation_risk" and metrics["horizon_days"] == 30
    for key in ("precision_at_5", "precision_at_10", "recall", "pr_auc"):
        assert isinstance(metrics[key], float) and 0 <= metrics[key] <= 1, key
    assert metrics["status"] == "ok" and metrics["test_cutoff"] == "2026-05-31"
    assert metrics["n_test_positives"] >= 2 and metrics["n_train_positives"] >= 5
    assert "simulated" in metrics["note"]
    assert 0 < metrics["base_rate"] < 1


def test_metrics_are_reproducible(pipeline, tmp_path):
    again = md.run(pipeline["db"], tmp_path / "m.json")
    assert again.metrics == json.loads(pipeline["metrics_path"].read_text(encoding="utf-8"))
    pd.testing.assert_frame_equal(
        again.scores.drop(columns="drivers"), pipeline["result"].scores.drop(columns="drivers")
    )


def test_precision_at_k_and_recall_on_a_known_ranking():
    test = pd.DataFrame({"provider_id": list("ABCDEF"), "label": [1, 0, 1, 0, 0, 1]})
    risk = pd.Series([0.9, 0.8, 0.7, 0.2, 0.1, 0.05])
    assert md.precision_at(test, risk, 2) == 0.5
    assert md.precision_at(test, risk, 3) == pytest.approx(2 / 3)


def test_no_positives_means_insufficient_data_not_a_guess(pipeline, tmp_path, caplog):
    db = tmp_path / "none.db"
    shutil.copy(pipeline["db"], db)
    con = sqlite3.connect(db)
    con.execute("DELETE FROM investigations WHERE outcome = 'confirmed'")
    con.commit()
    con.close()
    with caplog.at_level(logging.WARNING, logger="claimshield.predict"):
        result = md.run(db, tmp_path / "metrics.json")
    assert result.metrics["status"] == "Insufficient data"
    assert result.metrics["pr_auc"] is None and result.scores.empty
    assert "Insufficient data" in caplog.text


# ------------------------------------------------------------ the gate and scoring


def test_the_repeat_offender_gets_a_high_30_day_band(pipeline):
    scores = pipeline["result"].scores.set_index("provider_id")
    row = scores.loc["PRV-010"]
    assert row.horizon_days == 30 and row.risk_band == "High"
    assert row.band_source == "escalated" and row.prior_confirmed_count == 1 and row.trend >= 1.5
    assert "history rule" in row.band_reason
    assert 0 <= row.investigation_risk < md.HIGH_MIN  # the model's own number is left untouched


def test_only_providers_matching_the_history_rule_are_raised(pipeline):
    scores = pipeline["result"].scores
    raised = scores[scores.band_source == "escalated"]
    assert list(raised.provider_id) == ["PRV-010"]
    assert (raised.prior_confirmed_count >= 1).all() and (raised.trend >= 1.5).all()
    quiet = scores.set_index("provider_id").loc[["PRV-015", "PRV-A01"]]
    assert (quiet.risk_band != "High").all()  # honest specialist and the hidden ring


def test_every_provider_is_scored_with_ranked_bands_and_drivers(pipeline):
    scores = pipeline["result"].scores
    assert len(scores) == 40 and scores.provider_id.is_unique
    assert scores.investigation_risk.between(0, 1).all()
    assert scores.investigation_risk.is_monotonic_decreasing and list(scores.risk_rank) == list(range(1, 41))
    assert set(scores.risk_band) <= {"Low", "Medium", "High"}
    for drivers in scores.drivers:
        assert len(drivers) <= md.TOP_DRIVERS
        gains = [d["contribution"] for d in drivers]
        assert gains == sorted(gains, reverse=True) and all(g > 0 for g in gains)
    top = next(d for d in scores.drivers if d)[0]
    assert md.driver_text(top).endswith("risk)")
    assert not scores.low_confidence.any()  # 181 days of history at the as-of date


def test_short_history_is_flagged_low_confidence(pipeline, tmp_path):
    db = tmp_path / "early.db"
    shutil.copy(pipeline["db"], db)
    early = md.run(db, None, as_of=date(2026, 2, 28))
    assert early.scores.low_confidence.all() and (early.scores.history_days < ds.MIN_HISTORY_DAYS).all()


def test_scores_are_saved_to_the_investigation_risk_table(pipeline):
    con = sqlite3.connect(pipeline["db"])
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(investigation_risk)")]
        n = con.execute("SELECT COUNT(*) FROM investigation_risk").fetchone()[0]
    finally:
        con.close()
    assert n == 40 and {"investigation_risk", "risk_band", "band_source", "low_confidence", "drivers"} <= set(cols)
    assert not any(BANNED in c for c in cols)


def test_the_banned_name_appears_nowhere_in_the_codebase():
    hits = []
    skip = {".venv", "node_modules", ".git", "__pycache__", ".pytest_cache", ".ruff_cache", "dist"}
    for path in REPO.rglob("*"):
        if not path.is_file() or skip & set(path.relative_to(REPO).parts):
            continue
        if path.suffix in {".py", ".md", ".js", ".jsx", ".json", ".ps1", ".toml", ".example", ""}:
            try:
                if BANNED in path.read_text(encoding="utf-8", errors="ignore").lower():
                    hits.append(str(path.relative_to(REPO)))
            except OSError:
                continue
    assert hits == []


# ------------------------------------------------------------ case integration


def make_case(i, rule=0.0, anomaly_s=0.0, graph_s=0.0, inv=None, amount=0, members=0,
              severity="medium", fired=("rules",)):  # fmt: skip
    return Case(
        case_id=f"CASE-{i:04d}", case_type="provider", primary_entity=f"E{i}", entity_ids=[f"E{i}"],
        flagged_amount=amount, affected_members=[f"M{j}" for j in range(members)],
        detectors_fired=list(fired), rule_score=rule, anomaly_score=anomaly_s, graph_score=graph_s,
        investigation_risk=inv, worst_severity=severity,
    )  # fmt: skip


def test_case_risk_is_the_mean_of_four_scores_when_the_model_ran():
    a = make_case(1, rule=0.9, anomaly_s=0.6, inv=0.3, amount=100, members=4, severity="high",
                  fired=("rules", "anomaly"))  # fmt: skip
    b = make_case(2, rule=0.3, inv=0.6, amount=50, members=8)
    ranked = ranking.rank_cases([a, b]).set_index("case_id")
    # risk: a=(0.9+0.6+0+0.3)/4=0.45, b=(0.3+0.6)/4=0.225 -> scaled by max: 1.0 and 0.5
    assert ranked.loc["CASE-0001", "priority"] == pytest.approx(
        0.30 * 1.0 + 0.25 * 1.0 + 0.15 * 0.5 + 0.15 * 0.75 + 0.15 * (2 / 3), abs=1e-6
    )
    assert ranked.loc["CASE-0002", "priority"] == pytest.approx(
        0.30 * 0.5 + 0.25 * 0.5 + 0.15 * 1.0 + 0.15 * 0.5 + 0.15 * (1 / 3), abs=1e-6
    )


def test_case_risk_falls_back_to_three_scores_without_the_model():
    a = make_case(1, rule=0.9, anomaly_s=0.6)
    b = make_case(2, rule=0.3)
    ranked = ranking.rank_cases([a, b]).set_index("case_id")
    assert ranked.loc["CASE-0001", "risk_raw"] == pytest.approx(0.5)  # (0.9 + 0.6 + 0) / 3
    assert ranked.loc["CASE-0002", "risk_raw"] == pytest.approx(0.1)


def test_cases_carry_investigation_risk_and_the_ring_stays_first(pipeline):
    db = pipeline["db"]
    before_cases = builder.build_cases(db)
    assert all(c.investigation_risk is not None for c in before_cases)
    result = ranking.run(db)
    top = result.ranked.iloc[0]
    ring = next(c for c in result.cases if c.case_id == top.case_id)
    assert ring.case_type == "ring" and "PRV-A01" in ring.entity_ids and top["rank"] == 1
    scores = pipeline["result"].scores.set_index("provider_id")
    assert ring.investigation_risk == scores.loc["PRV-A01", "investigation_risk"]
    assert not any("PRV-015" in c.entity_ids for c in result.cases)  # honest specialist: no case
    repeat = next(c for c in result.cases if "PRV-010" in c.entity_ids)
    assert repeat.investigation_band == "High" and repeat.band_source == "escalated"
    con = sqlite3.connect(db)
    try:
        row = con.execute(
            "SELECT investigation_risk, investigation_band, band_source FROM cases "
            "WHERE primary_entity = 'PRV-010'"
        ).fetchone()
    finally:
        con.close()
    assert row[1] == "High" and row[2] == "escalated" and row[0] == repeat.investigation_risk


def test_investigation_risk_changes_the_priorities(pipeline, tmp_path):
    db = tmp_path / "without.db"
    shutil.copy(pipeline["db"], db)
    con = sqlite3.connect(db)
    con.execute("DROP TABLE investigation_risk")
    con.commit()
    con.close()
    without = ranking.run(db).ranked.set_index("case_id").priority
    with_model = ranking.run(pipeline["db"]).ranked.set_index("case_id").priority
    assert (without != with_model.reindex(without.index)).any()


def test_missing_model_output_is_logged_and_cases_still_build(pipeline, tmp_path, caplog):
    db = tmp_path / "nomodel.db"
    shutil.copy(pipeline["db"], db)
    con = sqlite3.connect(db)
    con.execute("DROP TABLE investigation_risk")
    con.commit()
    con.close()
    with caplog.at_level(logging.WARNING, logger="claimshield.cases"):
        cases = builder.build_cases(db)
    assert cases and all(c.investigation_risk is None for c in cases)
    assert "no investigation_risk table" in caplog.text
