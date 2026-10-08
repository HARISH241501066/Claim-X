import csv
import re
import sqlite3
from pathlib import Path

import pytest

from backend.data import generator as gen

BACKEND = Path(gen.__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("m1")
    counts = gen.generate(out / "t.db", out / "truth.csv")
    con = sqlite3.connect(out / "t.db")
    yield {"con": con, "counts": counts, "truth": out / "truth.csv", "dir": out}
    con.close()


def q(built, sql, *args):
    return built["con"].execute(sql, args).fetchall()


def scalar(built, sql, *args):
    return q(built, sql, *args)[0][0]


def test_row_counts_match_targets(built):
    assert built["counts"] == gen.TARGETS
    for table, target in gen.TARGETS.items():
        assert scalar(built, f"SELECT COUNT(*) FROM {table}") == target


def test_ring_members_shared_by_a01_b01_c01(built):
    n = scalar(
        built,
        """SELECT COUNT(*) FROM (
             SELECT member_id FROM claims WHERE provider_id='PRV-A01'
             INTERSECT SELECT member_id FROM claims WHERE facility_id='FAC-B01'
             INTERSECT SELECT member_id FROM claims WHERE facility_id='FAC-C01')""",
    )
    assert n == 15


def test_ring_owners_related_and_monthly_referrals(built):
    rel = dict(q(built, "SELECT owner_id, related_to FROM owners WHERE related_to IS NOT NULL"))
    assert rel == {"OWN-001": "OWN-002", "OWN-002": "OWN-001"}
    owners = dict(
        q(built, "SELECT facility_id, owner_id FROM facilities WHERE facility_id IN ('FAC-B01','FAC-C01')")
    )
    assert owners == {"FAC-B01": "OWN-001", "FAC-C01": "OWN-002"}
    months = scalar(
        built,
        """SELECT MIN(c) FROM (SELECT COUNT(DISTINCT substr(referral_date,1,7)) c
             FROM referrals WHERE from_provider_id='PRV-A01' GROUP BY member_id)""",
    )
    assert months == 6


def test_ring_claims_are_inflated(built):
    ring = scalar(
        built,
        """SELECT AVG(c.billed_amount * 1.0 / p.price_inr) FROM claims c
           JOIN procedure_codes p ON p.code = c.procedure_code
           WHERE c.facility_id IN ('FAC-B01','FAC-C01') AND c.provider_id='PRV-A01'""",
    )
    assert ring > 2.2


def test_upcoder_bills_level_5_on_70_percent(built):
    share = scalar(
        built,
        "SELECT AVG(code_level = 5) FROM claims WHERE provider_id='PRV-005' AND code_level IS NOT NULL",
    )
    assert 0.68 <= share <= 0.72
    peers = scalar(
        built,
        """SELECT AVG(code_level = 5) FROM claims WHERE code_level IS NOT NULL
           AND provider_id NOT IN ('PRV-005','PRV-015')""",
    )
    assert peers < 0.12


def test_double_billing_has_20_exact_duplicate_groups(built):
    groups = q(
        built,
        """SELECT COUNT(*) FROM claims GROUP BY member_id, provider_id, facility_id,
           service_date, procedure_code, billed_amount HAVING COUNT(*) > 1""",
    )
    assert len(groups) == 20
    assert all(n == 2 for (n,) in groups)


def test_unbundling_components_same_day_without_panel(built):
    rows = q(
        built,
        """SELECT provider_id, member_id, service_date, COUNT(DISTINCT procedure_code)
           FROM claims WHERE procedure_code IN
             (SELECT component_code FROM panel_components)
           GROUP BY provider_id, member_id, service_date""",
    )
    assert len(rows) == 12
    assert all(n == 4 for *_, n in rows)
    for provider, member, day, _ in rows:
        panel = scalar(
            built,
            """SELECT COUNT(*) FROM claims WHERE provider_id=? AND member_id=?
               AND service_date=? AND procedure_code='PNL-BMP'""",
            provider, member, day,
        )
        assert panel == 0


def test_component_prices_exceed_panel_price(built):
    total = scalar(
        built,
        """SELECT SUM(price_inr) FROM procedure_codes
           WHERE code IN (SELECT component_code FROM panel_components)""",
    )
    assert total > scalar(built, "SELECT price_inr FROM procedure_codes WHERE code='PNL-BMP'")


def test_phantom_claims_fall_inside_stays_elsewhere(built):
    rows = q(
        built,
        """SELECT c.claim_id FROM claims c JOIN inpatient_stays s ON s.member_id=c.member_id
           WHERE c.claim_type='outpatient' AND c.service_date BETWEEN s.admit_date AND s.discharge_date
           AND c.facility_id <> s.facility_id""",
    )
    assert len(rows) == 10
    assert scalar(
        built,
        """SELECT COUNT(*) FROM claims c JOIN inpatient_stays s ON s.member_id=c.member_id
           WHERE c.claim_type='outpatient' AND c.service_date BETWEEN s.admit_date AND s.discharge_date""",
    ) == 10


def test_overutilizers_have_about_25_sessions_a_month(built):
    heavy = q(
        built,
        """SELECT member_id, substr(service_date,1,7), COUNT(*) FROM claims
           WHERE procedure_code='PHY-SESSION' GROUP BY 1, 2 HAVING COUNT(*) > 4""",
    )
    assert len({m for m, _, _ in heavy}) == 3
    assert len(heavy) == 9
    assert all(n == 25 for *_, n in heavy)


def test_repeat_offender_history_and_rising_volume(built):
    outcome, closed = q(
        built, "SELECT outcome, closed_date FROM investigations WHERE entity_id='PRV-010'"
    )[0]
    assert outcome == "confirmed" and closed < "2026-01-01"
    monthly = dict(
        q(
            built,
            "SELECT substr(service_date,1,7), COUNT(*) FROM claims WHERE provider_id='PRV-010' GROUP BY 1",
        )
    )
    assert monthly["2026-05"] + monthly["2026-06"] > 2 * (monthly["2026-01"] + monthly["2026-02"])


def test_honest_specialist_is_busy_but_not_upcoding(built):
    assert scalar(built, "SELECT COUNT(*) FROM claims WHERE provider_id='PRV-015'") == 200
    share = scalar(built, "SELECT AVG(code_level = 5) FROM claims WHERE provider_id='PRV-015'")
    assert share < 0.25
    assert scalar(built, "SELECT outcome FROM investigations WHERE entity_id='PRV-015'") == "cleared"


def test_honest_followups_look_like_duplicates_but_are_not(built):
    truth = list(csv.DictReader(built["truth"].open()))
    follow = [r["entity_id"] for r in truth if r["scenario"] == "honest_followup"]
    assert len(follow) == 40
    marks = ",".join("?" * len(follow))
    pairs = q(
        built,
        f"""SELECT COUNT(*) FROM claims WHERE claim_id IN ({marks})
            GROUP BY member_id, provider_id, service_date HAVING COUNT(*) = 2""",
        *follow,
    )
    assert len(pairs) == 20


def test_investigations_outcomes(built):
    outcomes = {o for (o,) in q(built, "SELECT DISTINCT outcome FROM investigations")}
    assert outcomes == {"confirmed", "cleared"}


def test_referential_integrity(built):
    assert q(built, "PRAGMA foreign_key_check") == []


def test_ids_and_dates_are_well_formed(built):
    patterns = {
        "providers": ("provider_id", r"PRV-(A01|\d{3})"),
        "facilities": ("facility_id", r"FAC-([BC]01|\d{3})"),
        "owners": ("owner_id", r"OWN-\d{3}"),
        "members": ("member_id", r"MEM-\d{3}"),
        "claims": ("claim_id", r"CLM-\d{6}"),
        "investigations": ("case_id", r"CASE-\d{3}"),
    }
    for table, (col, pat) in patterns.items():
        for (value,) in q(built, f"SELECT {col} FROM {table}"):
            assert re.fullmatch(pat, value), (table, value)
    lo, hi = scalar(built, "SELECT MIN(service_date) FROM claims"), scalar(
        built, "SELECT MAX(service_date) FROM claims"
    )
    assert "2026-01-01" <= lo and hi <= "2026-06-30"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", lo)


def test_six_cities_and_codes_reference_data(built):
    assert scalar(built, "SELECT COUNT(*) FROM cities") == 6
    levels = {l for (l,) in q(built, "SELECT DISTINCT code_level FROM procedure_codes WHERE code_level")}
    assert levels == {1, 2, 3, 4, 5}
    assert scalar(built, "SELECT COUNT(*) FROM panel_components") == 4
    assert {t for (t,) in q(built, "SELECT DISTINCT type FROM facilities")} == {
        "lab", "clinic", "pharmacy", "DME"
    }


def test_ground_truth_file(built):
    rows = list(csv.DictReader(built["truth"].open()))
    assert list(rows[0]) == ["entity_id", "scenario"]
    assert {r["scenario"] for r in rows} == {
        "ring", "upcoder", "double_billing", "unbundling", "phantom", "overutilizer",
        "repeat_offender", "honest_specialist", "honest_followup",
    }
    tables = {t for (t,) in q(built, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any("truth" in t for t in tables)
    assert sum(r["scenario"] == "double_billing" for r in rows) == 20


def test_generation_is_deterministic(built, tmp_path):
    gen.generate(tmp_path / "again.db", tmp_path / "again.csv")
    assert (tmp_path / "again.csv").read_bytes() == built["truth"].read_bytes()
    other = sqlite3.connect(tmp_path / "again.db")
    try:
        for table in gen.TARGETS:
            sql = f"SELECT * FROM {table} ORDER BY 1"
            assert other.execute(sql).fetchall() == built["con"].execute(sql).fetchall()
    finally:
        other.close()


def test_ground_truth_not_used_outside_generator_and_tests():
    offenders = []
    for path in BACKEND.rglob("*.py"):
        parts = set(path.relative_to(BACKEND).parts)
        if ".venv" in parts or "tests" in parts or path.name == "generator.py":
            continue
        if "ground_truth" in path.read_text(encoding="utf-8"):
            offenders.append(str(path))
    assert offenders == []


def test_no_accidental_repeats_of_member_provider_code_date(built):
    groups = q(
        built,
        """SELECT COUNT(*) FROM claims GROUP BY member_id, provider_id, procedure_code,
           service_date HAVING COUNT(*) > 1""",
    )
    assert len(groups) == 20  # only the planted double-billing pairs


def test_normal_referrals_stay_in_the_members_city(built):
    far = scalar(
        built,
        """SELECT COUNT(*) FROM claims c JOIN members m ON m.member_id = c.member_id
           JOIN facilities f ON f.facility_id = c.facility_id
           WHERE c.referring_provider_id IS NOT NULL AND c.provider_id <> 'PRV-A01'
           AND f.city <> m.city""",
    )
    assert far == 0


def test_only_phantom_claims_are_served_outside_the_members_city(built):
    far = scalar(
        built,
        """SELECT COUNT(*) FROM claims c JOIN members m ON m.member_id = c.member_id
           JOIN facilities f ON f.facility_id = c.facility_id WHERE f.city <> m.city""",
    )
    assert far == 10  # the planted phantom claims
