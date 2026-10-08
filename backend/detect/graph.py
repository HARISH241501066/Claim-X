"""Referral/ownership graph analytics: overlap, self-referral, communities, ring findings.

Pipeline order: data -> rules engine -> features/anomaly -> graph. This module appends
detector="ring" findings (replacing earlier ring rows) and writes referral_pairs and
communities tables. Missing upstream tables are logged as "Insufficient data" and count as 0.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import community as community_louvain
import networkx as nx
import pandas as pd

from backend.detect.base import Finding
from backend.detect.engine import append_findings
from backend.features.provider_features import rule_hit_claims

log = logging.getLogger("claimshield.graph")
DB_PATH = Path(__file__).resolve().parents[1] / "claimshield.db"

SEED = 42
MIN_JACCARD = 0.4  # flag referral pairs above this ...
MIN_SHARED = 10  # ... with at least this many shared members
OWNERSHIP_WEIGHT = 5.0  # weight of owned_by / related_to edges in the entity-only graph
MIN_COMMUNITY_SIZE = 3
MEMBER_COLLAPSE_LIMIT = 20
TOP_COMMUNITIES = 3
MIN_RING_SCORE = 0.5
W_FLAGGED, W_RULE, W_ANOMALY, W_SELFREF = 0.3, 0.2, 0.3, 0.2  # community score weights
GRAPH_TABLES = ["claims", "providers", "facilities", "members", "owners", "referrals"]

COMMUNITIES_DDL = """
CREATE TABLE communities (
    community_id TEXT PRIMARY KEY, method TEXT NOT NULL, size INTEGER NOT NULL,
    nodes TEXT NOT NULL, n_claims INTEGER NOT NULL, flagged_amount INTEGER NOT NULL,
    rule_hits_per_100 REAL NOT NULL, mean_anomaly REAL NOT NULL, self_referral INTEGER NOT NULL,
    score REAL NOT NULL, score_rank INTEGER NOT NULL);
"""


def load_graph_tables(db_path: str | Path) -> dict[str, pd.DataFrame]:
    con = sqlite3.connect(db_path)
    try:
        return {t: pd.read_sql_query(f"SELECT * FROM {t}", con) for t in GRAPH_TABLES}
    finally:
        con.close()


def load_signals(db_path: str | Path) -> tuple[set[str], dict[str, float]]:
    """Rule-hit claim IDs and provider anomaly scores; empty (and logged) when missing."""
    con = sqlite3.connect(db_path)
    try:
        rule_claims = rule_hit_claims(con)
        try:
            rows = con.execute(
                "SELECT provider_id, anomaly_score FROM provider_anomaly_scores"
            ).fetchall()
        except sqlite3.OperationalError:
            log.warning("Insufficient data: no provider_anomaly_scores; mean anomaly set to 0")
            rows = []
    finally:
        con.close()
    return rule_claims, dict(rows)


# ---------------------------------------------------------------- the typed graph


def build_graph(tables: dict[str, pd.DataFrame]) -> nx.MultiDiGraph:
    """Typed graph. Edges: billed_for, referred_to, owned_by, related_to (weighted)."""
    g = nx.MultiDiGraph()
    for r in tables["providers"].itertuples():
        g.add_node(r.provider_id, type="provider", specialty=r.specialty, city=r.city)
    for r in tables["facilities"].itertuples():
        g.add_node(r.facility_id, type="facility", facility_type=r.type, city=r.city)
    for r in tables["members"].itertuples():
        g.add_node(r.member_id, type="member", city=r.city)
    for r in tables["owners"].itertuples():
        g.add_node(r.owner_id, type="owner")

    claims = tables["claims"]
    for column in ("provider_id", "facility_id"):
        grouped = claims.groupby([column, "member_id"]).billed_amount.agg(["size", "sum"])
        for (entity, member), row in grouped.iterrows():
            g.add_edge(
                entity, member, key="billed_for", type="billed_for",
                weight=int(row["size"]), amount=int(row["sum"]),
            )  # fmt: skip

    referred = claims[claims.referring_provider_id.notna()]
    amounts = referred.groupby(["referring_provider_id", "facility_id"]).billed_amount.sum()
    referrals = tables["referrals"]
    for (p, f), grp in referrals.groupby(["from_provider_id", "to_facility_id"]):
        g.add_edge(
            p, f, key="referred_to", type="referred_to", weight=len(grp),
            members=int(grp.member_id.nunique()), amount=int(amounts.get((p, f), 0)),
        )  # fmt: skip

    for r in tables["facilities"].itertuples():
        g.add_edge(r.facility_id, r.owner_id, key="owned_by", type="owned_by", weight=1)
    for r in tables["providers"].dropna(subset=["owner_id"]).itertuples():
        g.add_edge(r.provider_id, r.owner_id, key="owned_by", type="owned_by", weight=1)
    for r in tables["owners"].dropna(subset=["related_to"]).itertuples():
        g.add_edge(r.owner_id, r.related_to, key="related_to", type="related_to", weight=1)
    return g


# ---------------------------------------------------------------- overlap and self-referral


def related_owner_pairs(owners: pd.DataFrame) -> set[frozenset[str]]:
    rel = owners.dropna(subset=["related_to"])
    return {frozenset((a, b)) for a, b in zip(rel.owner_id, rel.related_to, strict=True)}


def referral_pairs(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Per referrer -> receiver pair: Jaccard overlap, overlap flag and self-referral flag.

    Member sets come from referrals: A = members the referrer referred anywhere,
    B = members referred to the receiver by anyone, shared = |A & B|.
    """
    ref = tables["referrals"]
    referred_by = ref.groupby("from_provider_id").member_id.apply(set)
    referred_to = ref.groupby("to_facility_id").member_id.apply(set)
    provider_owner = dict(zip(tables["providers"].provider_id, tables["providers"].owner_id, strict=True))
    facility_owner = dict(zip(tables["facilities"].facility_id, tables["facilities"].owner_id, strict=True))
    related = related_owner_pairs(tables["owners"])
    rows = []
    for (p, f), grp in ref.groupby(["from_provider_id", "to_facility_id"]):
        a, b = referred_by[p], referred_to[f]
        shared = len(a & b)
        jaccard = shared / len(a | b)
        po, fo = provider_owner.get(p), facility_owner.get(f)
        self_ref = bool(po and fo and (po == fo or frozenset((po, fo)) in related))
        rows.append(
            {
                "referrer": p, "receiver": f, "referrals": len(grp),
                "referrer_members": len(a), "receiver_members": len(b), "shared": shared,
                "jaccard": round(jaccard, 4),
                "flagged": bool(jaccard > MIN_JACCARD and shared >= MIN_SHARED),
                "self_referral": self_ref, "referrer_owner": po, "receiver_owner": fo,
            }
        )  # fmt: skip
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- communities


def entity_graph(tables: dict[str, pd.DataFrame], pairs: pd.DataFrame) -> nx.Graph:
    """Providers, facilities and owners only; referral edges weighted by shared members."""
    g = nx.Graph()
    g.add_nodes_from(tables["providers"].provider_id, type="provider")
    g.add_nodes_from(tables["facilities"].facility_id, type="facility")
    g.add_nodes_from(tables["owners"].owner_id, type="owner")

    def bump(u: str, v: str, w: float) -> None:
        g.add_edge(u, v, weight=g.get_edge_data(u, v, {}).get("weight", 0.0) + w)

    for r in pairs.itertuples():
        bump(r.referrer, r.receiver, max(r.shared, 1))
    for r in tables["facilities"].itertuples():
        bump(r.facility_id, r.owner_id, OWNERSHIP_WEIGHT)
    for r in tables["providers"].dropna(subset=["owner_id"]).itertuples():
        bump(r.provider_id, r.owner_id, OWNERSHIP_WEIGHT)
    for a, b in related_owner_pairs(tables["owners"]):
        bump(a, b, OWNERSHIP_WEIGHT)
    return g


def self_referral_subgraph(tables: dict[str, pd.DataFrame], pairs: pd.DataFrame) -> nx.Graph:
    """Entities tied together by self-referral: referrer, receiver and the owners behind them."""
    h = nx.Graph()
    related = related_owner_pairs(tables["owners"])
    for r in pairs[pairs.self_referral].itertuples():
        h.add_edge(r.referrer, r.receiver)
        h.add_edge(r.referrer, r.referrer_owner)
        h.add_edge(r.receiver, r.receiver_owner)
        if r.referrer_owner != r.receiver_owner and frozenset((r.referrer_owner, r.receiver_owner)) in related:
            h.add_edge(r.referrer_owner, r.receiver_owner)
    return h


@dataclass
class Communities:
    members: dict[int, list[str]] = field(default_factory=dict)  # community -> sorted nodes
    method: dict[int, str] = field(default_factory=dict)  # "louvain" | "self-referral-components"

    def of(self, node: str) -> int | None:
        return next((c for c, nodes in self.members.items() if node in nodes), None)


def louvain_partition(g: nx.Graph) -> dict[str, int]:
    return community_louvain.best_partition(g, weight="weight", random_state=SEED)


def detect_communities(
    g: nx.Graph,
    selfref: nx.Graph,
    partition_fn: Callable[[nx.Graph], dict[str, int]] = louvain_partition,
) -> Communities:
    """Louvain (seed 42). If it splits a self-referral component, that component is merged."""
    assignment = dict(partition_fn(g))
    fallback: set[int] = set()
    for component in sorted(nx.connected_components(selfref), key=lambda c: sorted(c)):
        nodes = [n for n in component if n in assignment]
        counts = Counter(assignment[n] for n in nodes)
        if len(counts) > 1:
            target = min(counts, key=lambda c: (-counts[c], c))
            log.info("Louvain split a self-referral component; merging %s", sorted(nodes))
            for n in nodes:
                assignment[n] = target
            fallback.add(target)
    groups: dict[int, list[str]] = defaultdict(list)
    for node, c in assignment.items():
        groups[c].append(node)
    ordered = sorted(groups.values(), key=lambda nodes: min(nodes))
    result = Communities()
    for i, nodes in enumerate(ordered, 1):
        result.members[i] = sorted(nodes)
        old = assignment[nodes[0]]
        result.method[i] = "self-referral-components" if old in fallback else "louvain"
    return result


def score_communities(
    comms: Communities,
    tables: dict[str, pd.DataFrame],
    pairs: pd.DataFrame,
    rule_claims: set[str],
    anomaly: dict[str, float],
) -> tuple[pd.DataFrame, dict[int, set[str]]]:
    """Score each community (size >= 3). Returns the score table and flagged claim IDs."""
    claims = tables["claims"]
    flagged_pairs = set(map(tuple, pairs[pairs.flagged][["referrer", "receiver"]].values))
    in_flagged_pair = [
        (p, f) in flagged_pairs
        for p, f in zip(claims.referring_provider_id, claims.facility_id, strict=True)
    ]
    flagged_claims = claims.claim_id.isin(rule_claims) | pd.Series(in_flagged_pair, index=claims.index)
    selfref_pairs = set(map(tuple, pairs[pairs.self_referral][["referrer", "receiver"]].values))
    rows, flagged_ids = [], {}
    for cid, nodes in comms.members.items():
        if len(nodes) < MIN_COMMUNITY_SIZE:
            continue
        node_set = set(nodes)
        mask = claims.provider_id.isin(node_set) | claims.facility_id.isin(node_set)
        in_community = claims[mask]
        flagged = in_community[flagged_claims[mask]]
        providers = [n for n in nodes if n.startswith("PRV-")]
        scores = [anomaly[p] for p in providers if p in anomaly]
        flagged_ids[cid] = set(flagged.claim_id)
        rows.append(
            {
                "community": cid, "method": comms.method[cid], "size": len(nodes), "nodes": nodes,
                "n_claims": len(in_community), "flagged_amount": int(flagged.billed_amount.sum()),
                "rule_hits_per_100": 100 * in_community.claim_id.isin(rule_claims).sum() / max(len(in_community), 1),
                "mean_anomaly": sum(scores) / len(scores) if scores else 0.0,
                "self_referral": any(p in node_set and f in node_set for p, f in selfref_pairs),
            }
        )  # fmt: skip
    table = pd.DataFrame(rows)
    if table.empty:
        return table, flagged_ids
    for col in ("flagged_amount", "rule_hits_per_100"):
        peak = table[col].max()
        table[col + "_n"] = table[col] / peak if peak > 0 else 0.0
    table["score"] = (
        W_FLAGGED * table.flagged_amount_n
        + W_RULE * table.rule_hits_per_100_n
        + W_ANOMALY * table.mean_anomaly
        + W_SELFREF * table.self_referral.astype(float)
    ).round(4)
    table = table.sort_values(["score", "community"], ascending=[False, True]).reset_index(drop=True)
    table["score_rank"] = table.index + 1
    return table, flagged_ids


# ---------------------------------------------------------------- findings


def _reason(row: pd.Series, pairs: pd.DataFrame, n_flagged_claims: int) -> str:
    nodes = set(row.nodes)
    inside = pairs[pairs.referrer.isin(nodes) & pairs.receiver.isin(nodes)]
    parts = [f"Referral network of {', '.join(row.nodes[:8])}{' and others' if row['size'] > 8 else ''}"]
    hot = inside[inside.flagged].sort_values("jaccard", ascending=False)
    if len(hot):
        top = hot.iloc[0]
        parts.append(
            f"{len(hot)} referral pair(s) with over {MIN_JACCARD:.0%} shared-patient overlap "
            f"(e.g. {top.referrer} to {top.receiver}: {top.jaccard:.0%}, {int(top.shared)} shared members)"
        )
    selfref = inside[inside.self_referral]
    if len(selfref):
        s = selfref.iloc[0]
        parts.append(
            f"self-referral: {s.referrer} (owner {s.referrer_owner}) refers to {s.receiver} "
            f"(owner {s.receiver_owner}, same or related owner)"
        )
    parts.append(
        f"Rs {int(row.flagged_amount):,} billed on {n_flagged_claims} flagged claims, "
        f"mean provider anomaly {row.mean_anomaly:.2f}"
    )
    return "; ".join(parts) + ". Suspicious network that warrants review."


def ring_findings(
    scores: pd.DataFrame, pairs: pd.DataFrame, flagged_ids: dict[int, set[str]]
) -> list[Finding]:
    findings = []
    for row in scores.itertuples():
        if len(findings) >= TOP_COMMUNITIES or row.score < MIN_RING_SCORE:
            break
        evidence = sorted(flagged_ids[row.community])
        if not evidence:
            log.info("Insufficient data: community %s has no flagged claims", row.community)
            continue
        series = pd.Series(row._asdict())
        findings.append(
            Finding(
                entity_id=f"RING-{len(findings) + 1:02d}",
                detector="ring",
                score=min(1.0, float(row.score)),
                severity="high" if row.score >= 0.7 else "medium",
                reason=_reason(series, pairs, len(evidence)),
                evidence_ids=evidence,
            )
        )
    return findings


# ---------------------------------------------------------------- UI subgraph


def subgraph(g: nx.MultiDiGraph, entity_ids: list[str]) -> dict:
    """Nodes and links for the UI; members collapse into one count node above the limit."""
    keep = [e for e in dict.fromkeys(entity_ids) if e in g]
    for missing in sorted(set(entity_ids) - set(keep)):
        log.info("subgraph: unknown entity %s skipped", missing)
    selected = set(keep)
    nodes = {n: {"id": n, **{k: v for k, v in g.nodes[n].items()}} for n in keep}
    links = []
    for u, v, data in g.edges(data=True):
        if u in selected and v in selected:
            links.append({"source": u, "target": v, "type": data["type"], "weight": data["weight"]})

    member_links = []
    for u in keep:
        if g.nodes[u]["type"] not in ("provider", "facility"):
            continue
        for _, v, data in g.out_edges(u, data=True):
            if g.nodes[v]["type"] == "member" and v not in selected:
                member_links.append((u, v, data))
    members = {v for _, v, _ in member_links}
    collapsed = len(members) > MEMBER_COLLAPSE_LIMIT
    if collapsed:
        per_entity = Counter(u for u, _, _ in member_links)
        nodes["MEMBERS"] = {"id": "MEMBERS", "type": "member_group", "count": len(members)}
        links += [
            {"source": u, "target": "MEMBERS", "type": "billed_for", "weight": n}
            for u, n in sorted(per_entity.items())
        ]
    else:
        for u, v, data in member_links:
            nodes.setdefault(v, {"id": v, **g.nodes[v]})
            links.append({"source": u, "target": v, "type": "billed_for", "weight": data["weight"]})
    return {
        "nodes": list(nodes.values()), "links": links,
        "member_count": len(members), "members_collapsed": collapsed,
    }  # fmt: skip


# ---------------------------------------------------------------- run


@dataclass
class GraphResult:
    pairs: pd.DataFrame
    communities: Communities
    scores: pd.DataFrame
    findings: list[Finding]


def save_results(db_path: str | Path, result: GraphResult) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute("DROP TABLE IF EXISTS referral_pairs")
        result.pairs.to_sql("referral_pairs", con, index=False)
        con.execute("DROP TABLE IF EXISTS communities")
        con.executescript(COMMUNITIES_DDL)
        con.executemany(
            "INSERT INTO communities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    f"COM-{r.community:02d}", r.method, int(r.size), json.dumps(r.nodes),
                    int(r.n_claims), int(r.flagged_amount), float(r.rule_hits_per_100),
                    float(r.mean_anomaly), int(r.self_referral), float(r.score), int(r.score_rank),
                )
                for r in result.scores.itertuples()
            ],
        )
        con.commit()
    finally:
        con.close()
    append_findings(db_path, "ring", result.findings)


def analyze(
    tables: dict[str, pd.DataFrame],
    rule_claims: set[str],
    anomaly: dict[str, float],
    partition_fn: Callable[[nx.Graph], dict[str, int]] = louvain_partition,
) -> GraphResult:
    pairs = referral_pairs(tables)
    comms = detect_communities(
        entity_graph(tables, pairs), self_referral_subgraph(tables, pairs), partition_fn
    )
    scores, flagged_ids = score_communities(comms, tables, pairs, rule_claims, anomaly)
    return GraphResult(pairs, comms, scores, ring_findings(scores, pairs, flagged_ids))


def run(db_path: str | Path = DB_PATH) -> GraphResult:
    tables = load_graph_tables(db_path)
    rule_claims, anomaly = load_signals(db_path)
    result = analyze(tables, rule_claims, anomaly)
    save_results(db_path, result)
    return result


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    res = run()
    flagged = res.pairs[res.pairs.flagged]
    print(f"referral pairs: {len(res.pairs)}, overlap-flagged: {len(flagged)}, "
          f"self-referral: {int(res.pairs.self_referral.sum())}")
    cols = ["community", "method", "size", "flagged_amount", "rule_hits_per_100", "mean_anomaly",
            "self_referral", "score", "score_rank"]  # fmt: skip
    print(res.scores[cols].head(6).round(3).to_string(index=False))
    for f in res.findings:
        print(f"- {f.entity_id} [{f.severity}] score {f.score}: {f.reason}")
