"""What an outside LLM is allowed to see, built from the evidence pack.

masker.py keeps only fields on its allowlist and replaces names with placeholders, so the pack
is first reshaped into exactly those fields (to_masker_pack). Nothing else leaves the server.
In this synthetic data the "names" are identifiers: members, providers and owners are people
(PERSON_n) and facilities are organisations (ORG_n).
"""

from __future__ import annotations

import re

from backend.brief.evidence import SAMPLE_CLAIMS, Pack
from backend.brief.masker import LeakError, Vault, assert_no_leak, mask_pack

# Member, provider, owner and facility identifiers, wherever they appear in the text.
IDENTIFIER = re.compile(r"\b(?:PRV|MEM|OWN|FAC)-[A-Z0-9]+\b")
PEOPLE_PREFIXES = ("PRV-", "MEM-", "OWN-")
PLACEHOLDER = re.compile(r"\b(?:PERSON|ORG|EMAIL|PHONE|AADHAAR|PAN)_\d+\b")


def _evidence(pack: Pack) -> list[dict]:
    findings = []
    for e in pack.evidence:
        reason = e.reason
        if e.scope == "provider-level":
            reason += f" (Provider-level signal across {len(e.claim_ids)} claims, not specific claims.)"
        findings.append(
            {
                "evidence_key": e.key, "detector": e.detector, "entity_id": e.entity_id,
                "severity": e.severity, "score": e.score, "reason": reason,
                "claim_ids": e.claim_ids[:SAMPLE_CLAIMS], "count": len(e.claim_ids),
                "evidence_ids": e.other_ids,
            }
        )  # fmt: skip
    return findings


def _network(pack: Pack) -> dict:
    net = pack.network
    nodes = [{"id": d["id"], "type": d["type"]} for d in net["entities"]]
    edges = []
    for o in net["owners"]:
        edges += [{"from": o["owner_id"], "to": owned, "relationship": "owns"} for owned in o["owns"]]
        if o["related_to"]:
            edges.append({"from": o["owner_id"], "to": o["related_to"], "relationship": "related owner"})
    for p in net["referral_pairs"]:
        link = "referral with self-referral link" if p["self_referral"] else "referral"
        edges.append(
            {"from": p["referrer"], "to": p["receiver"], "relationship": link,
             "shared_members": p["shared_members"], "overlap": p["jaccard"]}
        )  # fmt: skip
    return {"nodes": nodes, "edges": edges}


def _prediction(pack: Pack) -> dict:
    p = pack.prediction
    if not p["available"]:
        return {"reason": p["reason"]}
    return {
        "horizon_days": p["horizon_days"], "investigation_risk": p["investigation_risk"],
        "band": p["risk_band"], "band_source": p["band_source"], "reason": p["band_reason"],
        "drivers": p["top_drivers"],
    }  # fmt: skip


def to_masker_pack(pack: Pack) -> dict:
    """The pack reshaped into fields the masker's allowlist keeps (and nothing it would drop)."""
    conf = pack.confidence
    return {
        "case_id": pack.case_id,
        "case_type": pack.case_type,
        "reason": (
            f"Flagged claims total Rs {pack.flagged_amount:,} across {pack.n_members} members; "
            f"priority rank {pack.rank}. Worst severity {pack.worst_severity}. Detector groups "
            f"fired: {', '.join(pack.detectors_fired)}."
        ),
        "findings": _evidence(pack),
        "timeline": [
            {
                "date": t.date,
                "event": f"{t.kind}: {t.description} (through {t.end_date})",
                "count": t.count, "amount": t.amount, "evidence_key": ", ".join(t.evidence_keys),
            }
            for t in pack.timeline
        ],
        "network": _network(pack),
        "prediction": _prediction(pack),
        "confidence": f"Confidence level: {conf['level']}. Basis: {'; '.join(conf['reasons'])}.",
        "limitations": list(pack.limitations),
        "recommended_action": pack.recommended_action["text"],
    }


def _strings(value):
    if isinstance(value, dict):
        for inner in value.values():
            yield from _strings(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _strings(inner)
    elif isinstance(value, str):
        yield value


def find_identifiers(payload: dict) -> tuple[list[str], list[str]]:
    """Every person and organisation identifier in the payload, for the masker to replace."""
    found = {m.group(0) for text in _strings(payload) for m in IDENTIFIER.finditer(text)}
    people = sorted(i for i in found if i.startswith(PEOPLE_PREFIXES))
    orgs = sorted(i for i in found if i.startswith("FAC-"))
    return people, orgs


def assert_no_identifiers(masked: dict) -> None:
    """A second check, beside the masker's own: no identifier may survive in what is sent."""
    if any(IDENTIFIER.search(text) for text in _strings(masked)):
        raise LeakError("An identifier pattern survived masking")


def prepare(pack: Pack) -> tuple[dict, Vault]:
    """Mask the pack for an LLM. Raises LeakError (with the vault attached) if anything leaks."""
    payload = to_masker_pack(pack)
    people, orgs = find_identifiers(payload)
    masked, vault = mask_pack(payload, people, orgs)
    try:
        assert_no_leak(masked, vault)
        assert_no_identifiers(masked)
    except LeakError as exc:
        exc.vault = vault  # lets the caller log what was masked, never the values
        raise
    return masked, vault


def unknown_placeholders(text: str, vault: Vault) -> list[str]:
    """Placeholders in a reply that the vault never issued (the model made them up)."""
    return sorted({t for t in PLACEHOLDER.findall(text) if t not in vault.token_to_value})
