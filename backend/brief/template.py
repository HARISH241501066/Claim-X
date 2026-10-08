"""Deterministic brief: the same pack always renders the same seven sections.

This is both the default output and the fallback whenever the LLM path is unavailable or its
text fails validation. It describes behaviour as suspicious and as warranting review, and
never states or implies guilt.
"""

from __future__ import annotations

from backend.brief.evidence import SAMPLE_CLAIMS, EvidenceItem, Pack
from backend.cases.builder import DETECTOR_GROUPS, detector_group

SECTIONS = [
    "Summary",
    "Evidence",
    "Timeline",
    "Network context",
    "Confidence",
    "Limitations",
    "Recommended human-review action",
]
FINAL_LINE = "Final decision rests with the assigned investigator."


def cites(keys: list[str]) -> str:
    return "".join(f"[{k}]" for k in keys)


def _keys_for(pack: Pack, group: str) -> list[str]:
    return [e.key for e in pack.evidence if detector_group(e.detector) == group]


def _summary(pack: Pack) -> list[str]:
    entities = pack.network["entities"]
    if pack.case_type == "ring":
        subject = f"{len(pack.entity_ids)} linked entities ({', '.join(pack.entity_ids[:6])})"
    else:
        d = next((x for x in entities if x["id"] == pack.primary_entity), None)
        subject = (
            f"provider {pack.primary_entity} ({d['specialty']}, {d['city']})" if d
            else f"entity {pack.primary_entity}"
        )  # fmt: skip
    fired = [
        f"{g} {cites(_keys_for(pack, g))}" for g in DETECTOR_GROUPS if g in pack.detectors_fired
    ]
    claim_keys = [e.key for e in pack.evidence if e.scope == "claim-specific"]
    if pack.flagged_amount > 0:
        value = (
            f"The flagged claims total Rs {pack.flagged_amount:,} and involve "
            f"{pack.n_members} members {cites(claim_keys)}."
        )
    else:
        value = (
            "No claim-level value is attached, because the signal is provider-level "
            f"{cites([e.key for e in pack.evidence])}."
        )
    fired_text = (
        f"{len(pack.detectors_fired)} of {len(DETECTOR_GROUPS)} detector groups fired: "
        f"{'; '.join(fired)}."
    )
    caution = (
        "The behaviour is suspicious and warrants review by a human investigator; "
        "it is not a finding of wrongdoing."
    )
    return [f"Case {pack.case_id} concerns {subject}.", fired_text, value, caution]


def _evidence_line(item: EvidenceItem) -> str:
    head = f"- [{item.key}] {item.detector} ({item.severity}, score {item.score:.2f}): {item.reason}"
    if item.scope == "provider-level":
        tail = f"Scope: provider-level signal across {len(item.claim_ids)} claims of {item.entity_id}."
    else:
        sample = ", ".join(item.claim_ids[:SAMPLE_CLAIMS])
        tail = f"Claims: {len(item.claim_ids)} (for example {sample})." if item.claim_ids else ""
        if item.other_ids:
            tail = f"{tail} Linked records: {', '.join(item.other_ids[:SAMPLE_CLAIMS])}.".strip()
    return f"{head} {tail}".rstrip()


def _timeline(pack: Pack) -> list[str]:
    if not pack.timeline:
        return ["- No dated events can be derived from the cited evidence."]
    lines = []
    for e in pack.timeline:
        span = e.date if e.date == e.end_date else f"{e.date} to {e.end_date}"
        lines.append(f"- {span}: {e.description} {cites(e.evidence_keys)}")
    return lines


def _network(pack: Pack) -> list[str]:
    net = pack.network
    graph_keys = _keys_for(pack, "graph")
    by_type: dict[str, list[str]] = {}
    for d in net["entities"]:
        label = d["id"]
        if d["type"] == "provider":
            label += f" ({d['specialty']}, {d['city']})"
        elif d["type"] == "facility":
            label += f" ({d['facility_type']}, {d['city']})"
        by_type.setdefault(d["type"], []).append(label)
    plural = {"provider": "Providers", "facility": "Facilities", "owner": "Owners"}
    lines = [f"- {plural.get(t, t)}: {'; '.join(v)}" for t, v in sorted(by_type.items())]
    for o in net["owners"]:
        owns = ", ".join(o["owns"]) or "no entity in this case"
        related = f"; related to {o['related_to']}" if o["related_to"] else ""
        lines.append(f"- Owner {o['owner_id']} owns {owns}{related}.")
    if net["referral_pairs"]:
        for p in net["referral_pairs"]:
            flags = ", self-referral link" if p["self_referral"] else ""
            lines.append(
                f"- Referral {p['referrer']} -> {p['receiver']}: {p['shared_members']} shared "
                f"members, {p['jaccard']:.0%} overlap{flags} {cites(graph_keys)}".rstrip()
            )
    else:
        lines.append("- No flagged referral pairs link these entities.")
    lines.append(f"- {net['member_count']} members are affected by the flagged claims.")
    return lines


def _confidence(pack: Pack) -> list[str]:
    conf, pred = pack.confidence, pack.prediction
    lines = [f"Confidence level: {conf['level']}", f"Basis: {'; '.join(conf['reasons'])}."]
    if pred["available"]:
        source = " (band raised by the history rule)" if pred["band_source"] == "escalated" else ""
        lines.append(
            f"Prediction: {pack.horizon_days}-day investigation_risk for {pred['provider_id']} is "
            f"{pred['investigation_risk']:.2f}, band {pred['risk_band']}{source}. It estimates the "
            "chance of a confirmed investigation, not the chance of wrongdoing."
        )
        if pred["top_drivers"]:
            lines.append(f"Top drivers: {'; '.join(pred['top_drivers'])}.")
    else:
        lines.append(f"Prediction: {pred['reason']}.")
    return lines


def render_template(pack: Pack) -> str:
    """Render the seven-section brief from the pack alone."""
    sections = {
        "Summary": _summary(pack),
        "Evidence": [_evidence_line(e) for e in pack.evidence],
        "Timeline": _timeline(pack),
        "Network context": _network(pack),
        "Confidence": _confidence(pack),
        "Limitations": [f"- {text}" for text in pack.limitations],
        "Recommended human-review action": [pack.recommended_action["text"]],
    }
    meta = (
        f"Case type: {pack.case_type} ({pack.primary_entity}). Priority rank {pack.rank}, "
        f"queue {pack.queue}. Status: {pack.status}."
    )
    out = [f"# Investigation brief: {pack.case_id}", meta, ""]
    for title in SECTIONS:
        out += [f"## {title}", *sections[title], ""]
    out.append(FINAL_LINE)
    return "\n".join(out) + "\n"
