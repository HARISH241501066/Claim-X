"""A brief, a screen and a report always say what type of check found each piece of evidence."""

import io

import pypdf
import pytest
from fastapi.testclient import TestClient

from backend import pipeline
from backend.api.main import create_app
from backend.brief import generate
from backend.brief.evidence import build_pack
from backend.brief.labels import describe, rule_kind, rule_label
from backend.brief.llm_payload import prepare
from backend.brief.template import render_template
from backend.brief.validator import validate_brief
from backend.tests.auth_helpers import login

RING = "CASE-0001"


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    return pipeline.run_all(tmp_path_factory.mktemp("ruletype") / "claimx.db")


@pytest.fixture(scope="module")
def pack(shared):
    return build_pack(RING, db_path=shared.db_path)


def test_every_detector_has_a_plain_label_and_a_kind():
    assert describe("duplicate") == "Duplicate billing (claim rule)"
    assert describe("phantom") == "Billing during a hospital stay (claim rule)"
    assert describe("anomaly") == "Provider profile anomaly (anomaly model)"
    assert describe("ring") == "Referral ring (network analysis)"
    assert rule_kind("unbundling") == "claim rule"


def test_a_plug_in_rule_with_no_entry_still_gets_a_readable_name_and_the_claim_rule_kind():
    assert rule_label("weekend_billing") == "Weekend billing"
    assert describe("weekend_billing") == "Weekend billing (claim rule)"


def test_the_template_names_the_type_of_check_on_every_evidence_line(pack):
    text = render_template(pack)
    evidence = text.split("## Evidence")[1].split("## Timeline")[0]
    for item in pack.evidence:
        line = next(ln for ln in evidence.splitlines() if ln.startswith(f"- [{item.key}]"))
        assert describe(item.detector) in line, line
    assert "Referral ring (network analysis)" in evidence and "Provider profile anomaly (anomaly model)" in evidence


def test_the_summary_names_the_checks_inside_each_detector_group(pack):
    summary = render_template(pack).split("## Summary")[1].split("## Evidence")[0]
    assert "anomaly (provider profile anomaly)" in summary and "graph (referral ring)" in summary


def test_the_template_is_still_valid(pack):
    assert validate_brief(render_template(pack), pack) == []


def test_a_brief_that_does_not_name_the_type_of_check_is_rejected_and_says_which(pack):
    text = render_template(pack).replace("Referral ring (network analysis)", "something")
    problems = validate_brief(text, pack)
    ring_key = next(e.key for e in pack.evidence if e.detector == "ring")
    assert any(f"[{ring_key}]" in p and "Referral ring (network analysis)" in p for p in problems)
    assert len(problems) == 1  # only the missing type, nothing else


def test_the_llm_is_given_the_type_of_check_and_told_to_use_it(pack):
    masked, _ = prepare(pack)
    detectors = {f["evidence_key"]: f["detector"] for f in masked["findings"]}
    assert detectors == {e.key: describe(e.detector) for e in pack.evidence}  # not dropped by the masker
    assert "type of check that found it" in generate.SYSTEM_PROMPT
    assert "copied exactly from its detector field" in generate.SYSTEM_PROMPT


def test_the_api_returns_the_label_and_kind_for_each_finding(shared, tmp_path):
    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "a.db", runner=lambda p: shared)
    with TestClient(app) as client:
        login(client, "admin")
        findings = client.get(f"/cases/{RING}").json()["findings"]
        seen = {(f["detector"], f["rule_label"], f["rule_kind"]) for f in findings}
        assert ("ring", "Referral ring", "network analysis") in seen
        assert ("anomaly", "Provider profile anomaly", "anomaly model") in seen
        brief = client.get(f"/cases/{RING}/brief").json()["brief"]
        assert "Referral ring (network analysis)" in brief


def test_the_case_report_names_the_type_of_check(shared, tmp_path):
    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "a.db", runner=lambda p: shared)
    with TestClient(app) as client:
        login(client, "admin")
        pdf = client.get(f"/cases/{RING}/report.pdf").content
    text = " ".join(" ".join(p.extract_text().split()) for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    assert "Referral ring (network analysis)" in text
