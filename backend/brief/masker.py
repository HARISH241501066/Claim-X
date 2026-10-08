"""
Mask sensitive data in an evidence pack before it is sent to any LLM,
and restore it locally after the LLM replies.

Flow:
    masked_pack, vault = mask_pack(pack, people, orgs)
    assert_no_leak(masked_pack, vault)        # refuse to send if anything slipped through
    reply = call_llm(masked_pack)             # LLM only ever sees tokens like PERSON_1, ORG_2
    brief = unmask_text(reply, vault)         # names restored locally, never sent

Design:
- Allowlist, not blocklist: only fields listed in ALLOWED_KEYS are kept. Anything new
  or unexpected is dropped by default (fail safe).
- Known names (people, organisations) are replaced with consistent tokens, so the
  LLM can still say "PERSON_1 referred 15 members to ORG_2".
- Regex patterns catch identifiers that may hide inside free text
  (emails, phone numbers, Aadhaar-like and PAN-like numbers).
- The vault (token -> real value) never leaves this server.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field

# Fields the LLM is allowed to see. Everything else is removed.
ALLOWED_KEYS = {
    # structure
    "case", "case_id", "case_type", "findings", "timeline", "network", "prediction",
    "confidence", "limitations", "recommended_action", "edges", "nodes",
    # finding / event content
    "evidence_key", "detector", "rule", "severity", "score", "reason", "evidence_ids",
    "claim_ids", "date", "event", "amount", "amount_inr", "count", "ratio",
    "shared_members", "overlap", "relationship", "from", "to", "type", "id",
    "investigation_risk", "band", "band_source", "drivers", "horizon_days",
    "entity_id", "entities",
}

# Never sent, even if someone adds them to ALLOWED_KEYS by mistake.
FORBIDDEN_KEYS = {
    "name", "member_name", "provider_name", "owner_name", "facility_name",
    "age", "dob", "date_of_birth", "gender", "city", "address", "pincode",
    "phone", "email", "aadhaar", "pan", "diagnosis", "diagnosis_code",
    "lat", "lon", "latitude", "longitude",
}

PATTERNS = {
    "EMAIL": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "PHONE": re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)"),
    "AADHAAR": re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)"),
    "PAN": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
}


@dataclass
class Vault:
    """Token <-> real value mapping. Stays on the server."""
    token_to_value: dict[str, str] = field(default_factory=dict)
    value_to_token: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    dropped_keys: set[str] = field(default_factory=set)

    def token_for(self, value: str, kind: str) -> str:
        if value in self.value_to_token:
            return self.value_to_token[value]
        self.counters[kind] = self.counters.get(kind, 0) + 1
        token = f"{kind}_{self.counters[kind]}"
        self.token_to_value[token] = value
        self.value_to_token[value] = token
        return token


def _mask_text(text: str, vault: Vault) -> str:
    # Longest names first so "Dr. Anil Kumar" is replaced before "Anil".
    for value in sorted(vault.value_to_token, key=len, reverse=True):
        text = re.sub(re.escape(value), vault.value_to_token[value], text, flags=re.IGNORECASE)
    for kind, pattern in PATTERNS.items():
        text = pattern.sub(lambda m, k=kind: vault.token_for(m.group(0), k), text)
    return text


def _mask_value(value, vault: Vault):
    if isinstance(value, dict):
        out = {}
        for key, inner in value.items():
            if key in FORBIDDEN_KEYS or key not in ALLOWED_KEYS:
                vault.dropped_keys.add(key)
                continue
            out[key] = _mask_value(inner, vault)
        return out
    if isinstance(value, list):
        return [_mask_value(item, vault) for item in value]
    if isinstance(value, str):
        return _mask_text(value, vault)
    return value  # numbers, booleans, None


def mask_pack(pack: dict, people: list[str] | None = None,
              orgs: list[str] | None = None) -> tuple[dict, Vault]:
    """Return a masked copy of the evidence pack and the vault to restore it.

    people: real names of doctors, members, owners in this case
    orgs:   real names of labs, clinics, suppliers in this case
    """
    vault = Vault()
    for name in people or []:
        if name and name.strip():
            vault.token_for(name.strip(), "PERSON")
    for name in orgs or []:
        if name and name.strip():
            vault.token_for(name.strip(), "ORG")
    masked = _mask_value(copy.deepcopy(pack), vault)
    return masked, vault


def unmask_text(text: str, vault: Vault) -> str:
    """Restore real names in the LLM reply, locally."""
    for token in sorted(vault.token_to_value, key=len, reverse=True):
        text = text.replace(token, vault.token_to_value[token])
    return text


class LeakError(RuntimeError):
    pass


def assert_no_leak(masked_pack: dict, vault: Vault) -> None:
    """Raise LeakError if any real value or forbidden field survived masking.
    Call this right before every LLM request; never send on failure."""
    payload = json.dumps(masked_pack, ensure_ascii=False)
    for value in vault.value_to_token:
        if value.lower() in payload.lower():
            raise LeakError(f"Sensitive value still present ({vault.value_to_token[value]})")
    for key in FORBIDDEN_KEYS:
        if f'"{key}":' in payload:
            raise LeakError(f"Forbidden field still present: {key}")
    for kind, pattern in PATTERNS.items():
        if pattern.search(payload):
            raise LeakError(f"Unmasked {kind} pattern found")


def audit_record(vault: Vault, provider: str) -> dict:
    """What to store in llm_requests: token kinds and dropped field names, never values."""
    return {
        "provider": provider,
        "tokens_by_kind": dict(vault.counters),
        "dropped_fields": sorted(vault.dropped_keys),
    }
