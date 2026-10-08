import logging
import shutil
import sqlite3

import networkx as nx
import pandas as pd
import pytest

from backend.data import generator as gen
from backend.detect import anomaly, engine, graph
from backend.features.provider_features import rule_hit_claims

RING = ["PRV-A01", "FAC-B01", "FAC-C01", "OWN-001", "OWN-002"]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    out = tmp_path_factory.mktemp("m4")
    db = out / "t.db"
    gen.generate(db, out / "truth.csv")
    engine.run(db)
    anomaly.run(db)
    tables = graph.load_graph_tables(db)
    rule_claims, scores = graph.load_signals(db)
    result = graph.analyze(tables, rule_claims, scores)
    return {
        "db": db, "tables": tables, "rule_claims": rule_claims, "anomaly": scores,
        "result": result, "graph": graph.build_graph(tables),
    }  # fmt: skip


# ------------------------------------------------------------ typed graph


def test_graph_has_typed_nodes_and_edges(world):
    g = world["graph"]
    node_types = nx.get_node_attributes(g, "type")
    assert pd.Series(node_types).value_counts().to_dict() == {
        "member": 300, "provider": 40, "facility": 15, "owner": 20,
    }  # fmt: skip
    edge_types = {d["type"] for *_, d in g.edges(data=True)}
    assert edge_types == {"billed_for", "referred_to", "owned_by", "related_to"}
    assert g.number_of_edges() > 0


def test_edge_weights_are_claim_and_referral_counts(world):
    g, t = world["graph"], world["tables"]
    claims = t["claims"]
    member = world["tables"]["claims"].query("provider_id == 'PRV-A01'").member_id.iloc[0]
    expected = claims[(claims.provider_id == "PRV-A01") & (claims.member_id == member)]
    edge = g["PRV-A01"][member]["billed_for"]
    assert edge["weight"] == len(expected) and edge["amount"] == expected.billed_amount.sum()
    refs = t["referrals"].query("from_provider_id == 'PRV-A01' and to_facility_id == 'FAC-B01'")
    ref_edge = g["PRV-A01"]["FAC-B01"]["referred_to"]
    assert ref_edge["weight"] == len(refs) == 90 and ref_edge["members"] == 15
    assert ref_edge["amount"] > 0


def test_ownership_edges_cover_facilities_providers_and_related_owners(world):
    g = world["graph"]
    assert g["FAC-B01"]["OWN-001"]["owned_by"]["type"] == "owned_by"
    assert g["PRV-A01"]["OWN-001"]["owned_by"]
    assert g["OWN-001"]["OWN-002"]["related_to"] and g["OWN-002"]["OWN-001"]["related_to"]
    owned = [u for u, _, d in g.edges(data=True) if d["type"] == "owned_by"]
    assert len(owned) == 15 + 4  # every facility plus the four providers with an owner


# ------------------------------------------------------------ overlap and self-referral


def fixture_tables(referrals, owners=None, provider_owner=None):
    providers = pd.DataFrame(
        {"provider_id": ["P1", "P2"], "owner_id": [(provider_owner or {}).get("P1"), None]}
    )
    facilities = pd.DataFrame({"facility_id": ["F1", "F2"], "owner_id": ["O1", "O2"]})
    owners = owners if owners is not None else pd.DataFrame(
        {"owner_id": ["O1", "O2"], "related_to": [None, None]}
    )
    ref = pd.DataFrame(referrals, columns=["from_provider_id", "to_facility_id", "member_id"])
    return {"referrals": ref, "providers": providers, "facilities": facilities, "owners": owners}


def members(prefix, n):
    return [f"{prefix}{i}" for i in range(n)]


def test_jaccard_and_flag_thresholds():
    # P1 referred 10 members to F1; 5 other members were referred to F1 by P2
    rows = [("P1", "F1", m) for m in members("A", 10)] + [("P2", "F1", m) for m in members("B", 5)]
    pairs = graph.referral_pairs(fixture_tables(rows)).set_index(["referrer", "receiver"])
    p = pairs.loc[("P1", "F1")]
    assert p.shared == 10 and p.referrer_members == 10 and p.receiver_members == 15
    assert p.jaccard == pytest.approx(10 / 15, abs=1e-4) and bool(p.flagged)


def test_flag_requires_more_than_040_and_at_least_10_shared():
    # exactly 0.4: P1 referred 10, F1 got those 10 plus 15 others (10 / 25)
    rows = [("P1", "F1", m) for m in members("A", 10)] + [("P2", "F1", m) for m in members("B", 15)]
    p = graph.referral_pairs(fixture_tables(rows)).set_index(["referrer", "receiver"]).loc[("P1", "F1")]
    assert p.jaccard == pytest.approx(0.4, abs=1e-4) and not p.flagged  # strictly greater than 0.4
    # high Jaccard but only 9 shared members
    rows = [("P1", "F1", m) for m in members("A", 9)]
    p = graph.referral_pairs(fixture_tables(rows)).iloc[0]
    assert p.jaccard == 1.0 and p.shared == 9 and not p.flagged


def test_self_referral_same_related_and_unrelated_owner():
    rows = [("P1", "F1", "M1")]
    same = graph.referral_pairs(fixture_tables(rows, provider_owner={"P1": "O1"})).iloc[0]
    assert same.self_referral
    unrelated = graph.referral_pairs(fixture_tables(rows, provider_owner={"P1": "O2"})).iloc[0]
    assert not unrelated.self_referral
    owners = pd.DataFrame({"owner_id": ["O1", "O2"], "related_to": ["O2", "O1"]})
    related = graph.referral_pairs(fixture_tables(rows, owners, {"P1": "O2"})).iloc[0]
    assert related.self_referral
    no_owner = graph.referral_pairs(fixture_tables(rows)).iloc[0]
    assert not no_owner.self_referral


def test_ring_pairs_are_flagged_with_the_expected_overlap(world):
    pairs = world["result"].pairs.set_index(["referrer", "receiver"])
    for receiver, low in (("FAC-B01", 0.9), ("FAC-C01", 0.8)):
        row = pairs.loc[("PRV-A01", receiver)]
        assert row.shared == 15 and row.jaccard >= low
        assert row.flagged and row.self_referral


def test_owner_operators_show_self_referral_without_being_a_ring(world):
    pairs = world["result"].pairs
    selfref_referrers = set(pairs[pairs.self_referral].referrer)
    assert "PRV-A01" in selfref_referrers
    assert selfref_referrers <= {"PRV-A01", "PRV-003", "PRV-012", "PRV-030"}


# ------------------------------------------------------------ the gate


def test_ring_entities_share_one_community(world):
    comms = world["result"].communities
    ids = {comms.of(node) for node in RING}
    assert len(ids) == 1 and None not in ids
    assert comms.method[ids.pop()] == "louvain"


def test_ring_community_has_the_highest_score(world):
    result = world["result"]
    top = result.scores.iloc[0]
    assert set(RING) <= set(top.nodes)
    assert top.score_rank == 1 and top.self_referral
    assert top.score > result.scores.iloc[1].score + 0.1  # clear margin, not a photo finish


def test_other_communities_with_self_referral_score_lower(world):
    scores = world["result"].scores
    others = scores[~scores.nodes.map(lambda n: "PRV-A01" in n)]
    assert (others.score < scores.iloc[0].score).all()


def test_small_communities_are_not_scored(world):
    assert (world["result"].scores["size"] >= graph.MIN_COMMUNITY_SIZE).all()


def test_louvain_is_deterministic(world):
    again = graph.analyze(world["tables"], world["rule_claims"], world["anomaly"])
    assert again.communities.members == world["result"].communities.members
    pd.testing.assert_frame_equal(
        again.scores.drop(columns="nodes"), world["result"].scores.drop(columns="nodes")
    )


@pytest.mark.parametrize("weight", [3.0, 5.0, 10.0])
def test_ring_stays_together_for_reasonable_ownership_weights(world, monkeypatch, weight):
    monkeypatch.setattr(graph, "OWNERSHIP_WEIGHT", weight)
    result = graph.analyze(world["tables"], world["rule_claims"], world["anomaly"])
    assert len({result.communities.of(n) for n in RING}) == 1


def test_fallback_merges_the_ring_when_louvain_splits_it(world):
    tables = world["tables"]

    def splitting_partition(g):  # a partition that scatters every ring entity
        return {node: (hash(node) % 1) + i for i, node in enumerate(sorted(g.nodes))}

    result = graph.analyze(tables, world["rule_claims"], world["anomaly"], splitting_partition)
    ids = {result.communities.of(n) for n in RING}
    assert len(ids) == 1
    assert result.communities.method[ids.pop()] == "self-referral-components"
    assert set(RING) <= set(result.scores.iloc[0].nodes)  # still scored as one network


# ------------------------------------------------------------ findings


def test_ring_findings_are_top_communities_with_evidence_and_careful_wording(world):
    findings = world["result"].findings
    assert 1 <= len(findings) <= graph.TOP_COMMUNITIES
    claim_ids = set(world["tables"]["claims"].claim_id)
    for f in findings:
        assert f.detector == "ring" and f.entity_id.startswith("RING-")
        assert f.evidence_ids and set(f.evidence_ids) <= claim_ids
        assert f.score >= graph.MIN_RING_SCORE and f.severity in ("medium", "high")
        assert "warrants review" in f.reason and "fraud" not in f.reason.lower()
    first = findings[0]
    assert first.severity == "high" and all(n in first.reason for n in ("PRV-A01", "FAC-B01"))
    assert "self-referral" in first.reason and "shared-patient overlap" in first.reason


def test_ring_evidence_covers_the_referred_claims(world):
    claims = world["tables"]["claims"]
    referred = claims[
        (claims.referring_provider_id == "PRV-A01") & claims.facility_id.isin(["FAC-B01", "FAC-C01"])
    ]
    assert len(referred) == 180
    assert set(referred.claim_id) <= set(world["result"].findings[0].evidence_ids)


def test_save_appends_ring_findings_and_keeps_other_detectors(world, tmp_path):
    db = tmp_path / "g.db"
    shutil.copy(world["db"], db)
    before = engine.load_findings(db)
    result = graph.run(db)
    graph.run(db)  # a second run replaces ring rows rather than duplicating them
    after = engine.load_findings(db)
    kept = [r for r in after if r["detector"] != "ring"]
    assert [r["finding_id"] for r in kept] == [r["finding_id"] for r in before]
    ring_rows = [r for r in after if r["detector"] == "ring"]
    assert len(ring_rows) == len(result.findings) >= 1
    assert len({r["finding_id"] for r in after}) == len(after)
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM referral_pairs").fetchone()[0] == len(result.pairs)
        top = con.execute("SELECT community_id, score_rank FROM communities ORDER BY score DESC").fetchone()
        assert top[1] == 1
        assert RING[0] in con.execute("SELECT nodes FROM communities WHERE score_rank = 1").fetchone()[0]
    finally:
        con.close()


def test_rule_hit_claims_ignore_graph_findings(world, tmp_path):
    db = tmp_path / "g2.db"
    shutil.copy(world["db"], db)
    con = sqlite3.connect(db)
    baseline = rule_hit_claims(con)
    con.close()
    graph.run(db)
    con = sqlite3.connect(db)
    try:
        assert rule_hit_claims(con) == baseline
    finally:
        con.close()


def test_missing_upstream_tables_are_logged_not_guessed(world, tmp_path, caplog):
    db = tmp_path / "bare.db"
    gen.generate(db, tmp_path / "t.csv")  # no rules run, no anomaly scores
    with caplog.at_level(logging.WARNING, logger="claimshield.features"):
        rule_claims, scores = graph.load_signals(db)
    assert rule_claims == set() and scores == {}
    assert "Insufficient data" in caplog.text
    result = graph.analyze(graph.load_graph_tables(db), rule_claims, scores)
    assert not result.scores.empty
    assert len({result.communities.of(n) for n in RING}) == 1  # structure needs no upstream data


# ------------------------------------------------------------ UI subgraph


def test_subgraph_of_the_ring_returns_typed_nodes_and_links(world):
    sub = graph.subgraph(world["graph"], RING)
    ids = {n["id"] for n in sub["nodes"]}
    assert set(RING) <= ids
    types = {n["id"]: n["type"] for n in sub["nodes"]}
    assert types["PRV-A01"] == "provider" and types["OWN-001"] == "owner"
    link_types = {link["type"] for link in sub["links"]}
    assert {"referred_to", "owned_by", "related_to"} <= link_types
    assert sub["members_collapsed"] == (sub["member_count"] > graph.MEMBER_COLLAPSE_LIMIT)
    assert all(link["source"] in ids and link["target"] in ids for link in sub["links"])


def test_members_are_collapsed_into_a_count_above_20(world):
    sub = graph.subgraph(world["graph"], ["PRV-005"])  # the upcoder bills for ~80 members
    assert sub["members_collapsed"] and sub["member_count"] > 20
    group = [n for n in sub["nodes"] if n["type"] == "member_group"]
    assert len(group) == 1 and group[0]["count"] == sub["member_count"]
    assert not any(n["type"] == "member" for n in sub["nodes"])
    link = next(link for link in sub["links"] if link["target"] == "MEMBERS")
    assert link["weight"] == sub["member_count"]


def test_few_members_are_shown_individually(world):
    claims = world["tables"]["claims"]
    small = claims.groupby("provider_id").member_id.nunique().sort_values().index[0]
    count = claims[claims.provider_id == small].member_id.nunique()
    assert count <= 20
    sub = graph.subgraph(world["graph"], [small])
    assert not sub["members_collapsed"] and sub["member_count"] == count
    assert sum(n["type"] == "member" for n in sub["nodes"]) == count


def test_subgraph_skips_unknown_ids_and_handles_empty_input(world, caplog):
    with caplog.at_level(logging.INFO, logger="claimshield.graph"):
        sub = graph.subgraph(world["graph"], ["PRV-A01", "NOPE-1"])
    assert "unknown entity NOPE-1" in caplog.text
    assert [n["id"] for n in sub["nodes"] if n["type"] == "provider"] == ["PRV-A01"]
    empty = graph.subgraph(world["graph"], [])
    assert empty == {"nodes": [], "links": [], "member_count": 0, "members_collapsed": False}
