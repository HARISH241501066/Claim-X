import logging
import sqlite3

import pandas as pd
import pytest

from backend.data import generator as gen
from backend.detect import engine
from backend.features import provider_features as pf

PROVIDERS = pd.DataFrame(
    {"provider_id": ["P1", "P2"], "specialty": ["General Medicine"] * 2}
)


def claim(cid, member, provider, day, amount, level, referring=None):
    return {
        "claim_id": cid, "member_id": member, "provider_id": provider, "service_date": day,
        "billed_amount": amount, "code_level": level, "referring_provider_id": referring,
    }  # fmt: skip


CLAIMS = pd.DataFrame(
    [
        claim("C1", "M1", "P1", "2026-01-03", 1000, 5),  # Saturday
        claim("C2", "M1", "P1", "2026-01-10", 2000, 3, "PRV-X"),  # Saturday
        claim("C3", "M2", "P1", "2026-02-02", 3000, 5),  # Monday
        claim("C4", "M1", "P1", "2026-02-03", 4000, None),  # Tuesday, no level
        claim("C5", "M3", "P2", "2026-01-05", 1000, 1),
        claim("C6", "M4", "P2", "2026-01-06", 3000, 2),
    ]
)


def test_features_match_hand_computed_values():
    f = pf.compute_features(CLAIMS, PROVIDERS, rule_hits={"C1", "C2"}, window_days=100)
    p1, p2 = f.loc["P1"], f.loc["P2"]
    # specialty median amount = median(1000,2000,3000,4000,1000,3000) = 2500
    assert p1.n_claims == 4 and p2.n_claims == 2
    assert p1.claims_per_day == pytest.approx(0.04)
    assert p1.avg_amount_ratio == pytest.approx(1.0) and p2.avg_amount_ratio == pytest.approx(0.8)
    assert p1.level5_share == pytest.approx(2 / 3) and p2.level5_share == 0
    assert p1.services_per_member_month == pytest.approx(4 / 3)  # (M1,Jan) (M1,Feb) (M2,Feb)
    assert p2.services_per_member_month == pytest.approx(1.0)
    assert p1.repeat_member_ratio == pytest.approx(0.5) and p2.repeat_member_ratio == 0
    assert p1.referred_claim_share == pytest.approx(0.25) and p2.referred_claim_share == 0
    assert p1.weekend_ratio == pytest.approx(0.5) and p2.weekend_ratio == 0
    assert p1.rule_hits_per_100 == pytest.approx(50) and p2.rule_hits_per_100 == 0
    assert list(f.columns[:3]) == ["specialty", "n_claims", "claims_per_day"]
    assert set(pf.FEATURES) <= set(f.columns)


def test_provider_without_levelled_claims_gets_zero_level5_share():
    claims = CLAIMS[CLAIMS.provider_id == "P1"].assign(code_level=None)
    f = pf.compute_features(claims, PROVIDERS)
    assert f.loc["P1"].level5_share == 0


def test_missing_findings_table_is_handled_safely(caplog):
    con = sqlite3.connect(":memory:")
    with caplog.at_level(logging.WARNING, logger="claimshield.features"):
        assert pf.rule_hit_claims(con) == set()
    assert "Insufficient data" in caplog.text


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("m3f")
    gen.generate(out / "t.db", out / "truth.csv")
    engine.run(out / "t.db")
    features = pf.build_provider_features(out / "t.db")
    return {"db": out / "t.db", "features": features}


def test_provider_features_table_is_written_for_every_provider(built):
    con = sqlite3.connect(built["db"])
    try:
        table = pd.read_sql_query("SELECT * FROM provider_features", con)
    finally:
        con.close()
    assert len(table) == 40
    assert set(pf.FEATURES) <= set(table.columns)
    assert not table[pf.FEATURES].isna().any().any()


def test_planted_behaviour_shows_up_in_the_features(built):
    f = built["features"]
    assert f.loc["PRV-005"].level5_share == pytest.approx(0.70)  # upcoder
    assert f.loc["PRV-A01"].referred_claim_share == pytest.approx(2 / 3)  # ring referrer
    assert f.loc["PRV-019"].services_per_member_month > 3  # physio over-utilization
    assert f.loc["PRV-005"].rule_hits_per_100 == pytest.approx(70.0)  # upcoding evidence
    assert f.loc["PRV-A01"].rule_hits_per_100 == 0  # the ring is invisible to the rules
    assert f.loc["PRV-015"].claims_per_day > 1  # the busy honest specialist
