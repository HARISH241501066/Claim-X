"""Investigation risk for 30, 60 and 90 days: one model per window, honest about thin data."""

import io
import json
import sqlite3

import pandas as pd
import pypdf
import pytest
from fastapi.testclient import TestClient

from backend import pipeline
from backend.api import views
from backend.api.main import create_app
from backend.brief.evidence import build_pack, predictions_for
from backend.cases import builder
from backend.data import generator as gen
from backend.detect import anomaly, engine, graph
from backend.predict import dataset as ds
from backend.predict import model as md
from backend.tests.auth_helpers import login

REPEAT_OFFENDER = "PRV-010"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("horizons")
    db = out / "t.db"
    gen.generate(db, out / "truth.csv")
    engine.run(db)
    anomaly.run(db)
    graph.run(db)
    metrics_path = out / "metrics.json"
    result = md.run_all(db, metrics_path)
    return {"db": db, "result": result, "metrics_path": metrics_path, "dir": out}


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    return pipeline.run_all(tmp_path_factory.mktemp("horizons_api") / "claimshield.db")


# ------------------------------------------------------------ which history each window learns from


def test_the_30_day_model_keeps_exactly_its_old_cutoffs_and_the_longer_ones_add_january():
    assert ds.cutoffs_for(30) == ds.CUTOFFS
    for horizon in (60, 90):
        assert ds.cutoffs_for(horizon) == [ds.EARLY_CUTOFF, *ds.CUTOFFS]
    assert ds.EARLY_CUTOFF.isoformat() == "2026-01-31"


def test_a_window_that_runs_past_the_end_of_the_data_is_censored():
    labels = ds.label_columns(pd.Index(["P"]), pd.DataFrame(columns=["entity_id", "entity_type", "outcome", "opened_date"]), ds.CUTOFFS[-1])
    assert bool(labels.censored_30.iloc[0]) is False  # May 31 + 30 days is Jun 30, the last day
    assert bool(labels.censored_60.iloc[0]) and bool(labels.censored_90.iloc[0])
    apr = ds.label_columns(pd.Index(["P"]), pd.DataFrame(columns=["entity_id", "entity_type", "outcome", "opened_date"]), ds.CUTOFFS[2])
    assert not bool(apr.censored_60.iloc[0]) and bool(apr.censored_90.iloc[0])


def test_each_window_trains_only_on_cutoffs_whose_window_was_fully_observed(built):
    horizons = json.loads(built["metrics_path"].read_text(encoding="utf-8"))["horizons"]
    assert horizons["30"]["train_cutoffs"] == ["2026-02-28", "2026-03-31", "2026-04-30"]
    assert horizons["30"]["test_cutoff"] == "2026-05-31"
    assert horizons["60"]["train_cutoffs"] == ["2026-01-31", "2026-02-28", "2026-03-31"]
    assert horizons["60"]["test_cutoff"] == "2026-04-30"  # 30 Apr + 60 days still ends on 29 Jun
    assert horizons["90"]["train_cutoffs"] == ["2026-01-31", "2026-02-28"]
    assert horizons["90"]["test_cutoff"] == "2026-03-31"  # the last cutoff with 90 observed days
    for key, h in horizons.items():
        assert h["horizon_days"] == int(key) and h["target"] == "investigation_risk" and h["status"] == "ok"
        assert h["test_cutoff"] not in h["train_cutoffs"]  # the test is always later than the training


def test_metrics_json_keeps_the_30_day_numbers_on_top_and_lists_every_window(built):
    top = json.loads(built["metrics_path"].read_text(encoding="utf-8"))
    for key in ("precision_at_5", "precision_at_10", "recall", "pr_auc", "base_rate", "note"):
        assert key in top and top[key] == top["horizons"]["30"][key]
    assert set(top["horizons"]) == {"30", "60", "90"}
    assert all("not real-world accuracy" in h["note"] for h in top["horizons"].values())  # said every time


def test_the_30_day_result_is_identical_to_the_old_single_window_run(built, tmp_path):
    single = tmp_path / "single.db"
    gen.generate(single, None)
    engine.run(single)
    anomaly.run(single)
    graph.run(single)
    alone = md.run(single, None)
    pd.testing.assert_frame_equal(alone.scores.reset_index(drop=True), built["result"].scores.reset_index(drop=True))
    assert {k: v for k, v in built["result"].metrics.items() if k != "horizons"} == alone.metrics


# ------------------------------------------------------------ scores for three windows


def test_every_provider_has_a_score_for_each_window(built):
    con = sqlite3.connect(built["db"])
    try:
        rows = con.execute("SELECT horizon_days, COUNT(*), COUNT(DISTINCT provider_id) FROM investigation_risk GROUP BY 1").fetchall()
        assert rows == [(30, 40, 40), (60, 40, 40), (90, 40, 40)]
        columns = {c[1] for c in con.execute("PRAGMA table_info(investigation_risk)")}
    finally:
        con.close()
    assert "investigation_risk" in columns and not any("fraud" in c for c in columns)
    assert sorted(built["result"].by_horizon) == [30, 60, 90]
    for h, res in built["result"].by_horizon.items():
        assert (res.scores.horizon_days == h).all() and res.scores.investigation_risk.between(0, 1).all()


def test_the_repeat_offender_is_high_in_every_window_and_the_band_says_why(built):
    for h, res in built["result"].by_horizon.items():
        row = res.scores.set_index("provider_id").loc[REPEAT_OFFENDER]
        assert row.risk_band == "High" and row.band_source == "escalated", h
        assert "history rule" in row.band_reason and row.prior_confirmed_count == 1
        assert 0 <= row.investigation_risk < md.HIGH_MIN  # the model's own number is left untouched


def test_running_one_window_again_keeps_the_others(built):
    md.run(built["db"], None, horizon=30)
    con = sqlite3.connect(built["db"])
    try:
        assert con.execute("SELECT COUNT(*) FROM investigation_risk").fetchone()[0] == 120
    finally:
        con.close()


def test_a_window_with_no_positive_label_says_insufficient_data_and_the_others_still_work(built, monkeypatch):
    real = md.build_dataset

    def without_positives(db, cutoffs=None):
        frame = real(db, cutoffs)
        frame["label_90"] = 0  # nobody was investigated within 90 days, as far as this data shows
        return frame

    monkeypatch.setattr(md, "build_dataset", without_positives)
    out = md.run_all(built["db"], None)
    assert out.by_horizon[90].scores.empty and out.by_horizon[90].metrics["status"] == "Insufficient data"
    assert out.by_horizon[90].metrics["precision_at_5"] is None  # no number is invented
    assert not out.by_horizon[30].scores.empty and not out.by_horizon[60].scores.empty


def test_case_priority_still_uses_the_30_day_risk_only(built):
    cases = builder.build_cases(built["db"])
    thirty = built["result"].by_horizon[30].scores.set_index("provider_id").investigation_risk
    ninety = built["result"].by_horizon[90].scores.set_index("provider_id").investigation_risk
    case = next(c for c in cases if c.primary_entity == REPEAT_OFFENDER)
    assert case.investigation_risk == thirty[REPEAT_OFFENDER] != ninety[REPEAT_OFFENDER]


# ------------------------------------------------------------ the API, the brief and the report


def test_a_case_returns_all_three_windows_and_keeps_the_30_day_field(shared, tmp_path):
    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "a.db", runner=lambda p: shared)
    with TestClient(app) as client:
        login(client, "admin")
        case_id = next(c.case_id for c in shared.cases if c.primary_entity == REPEAT_OFFENDER)
        body = client.get(f"/cases/{case_id}").json()
        assert set(body["predictions"]) == {"30", "60", "90"}
        assert all(p["available"] and p["horizon_days"] == int(k) for k, p in body["predictions"].items())
        assert body["prediction"] == body["predictions"]["30"]
        assert {p["risk_band"] for p in body["predictions"].values()} == {"High"}
        assert all(p["band_source"] == "escalated" for p in body["predictions"].values())
        brief = client.get(f"/cases/{case_id}/brief", params={"horizon": 60})
        assert brief.status_code == 200 and brief.json()["horizon_days"] == 60


def test_the_pack_for_a_longer_window_uses_that_windows_estimate(shared):
    case = next(c for c in shared.cases if c.primary_entity == REPEAT_OFFENDER)
    values = {h: build_pack(case.case_id, h, shared.db_path).prediction["investigation_risk"] for h in (30, 60, 90)}
    assert len(set(values.values())) == 3  # three different models, three different numbers
    assert predictions_for(shared.db_path, case.entity_ids)[90]["investigation_risk"] == values[90]


def test_a_longer_window_that_comes_out_lower_gets_a_note_and_the_number_is_not_changed(shared, monkeypatch):
    def fake(db, entities, horizons=(30, 60, 90)):
        base = {"available": True, "provider_id": "PRV-X", "risk_band": "Low", "band_source": "model", "band_reason": "m",
                "top_drivers": [], "history_days": 100, "low_confidence": False}  # fmt: skip
        return {30: {**base, "horizon_days": 30, "investigation_risk": 0.5},
                60: {**base, "horizon_days": 60, "investigation_risk": 0.2},
                90: {**base, "horizon_days": 90, "investigation_risk": 0.6}}  # fmt: skip

    monkeypatch.setattr(views, "predictions_for", fake)
    out = views._windows(shared, shared.cases[0], {"available": False})
    assert out["60"].note and "Lower than the 30-day estimate" in out["60"].note
    assert out["60"].investigation_risk == 0.2  # shown as the model gave it
    assert out["30"].note is None and out["90"].note is None


def test_a_missing_window_is_reported_as_insufficient_data_not_guessed(tmp_path):
    db = tmp_path / "empty.db"
    con = sqlite3.connect(db)
    con.close()
    out = predictions_for(db, ["PRV-001"])
    assert set(out) == {30, 60, 90} and all(not p["available"] and "Insufficient data" in p["reason"] for p in out.values())


def test_the_case_report_lists_every_window(shared, tmp_path):
    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "a.db", runner=lambda p: shared)
    with TestClient(app) as client:
        login(client, "admin")
        case_id = next(c.case_id for c in shared.cases if c.primary_entity == REPEAT_OFFENDER)
        pdf = client.get(f"/cases/{case_id}/report.pdf")
        text = " ".join(" ".join(p.extract_text().split()) for p in pypdf.PdfReader(io.BytesIO(pdf.content)).pages)
    for days in (30, 60, 90):
        assert f"{days}-day investigation risk" in text
