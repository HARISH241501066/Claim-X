import pytest

from backend.brief.masker import LeakError, assert_no_leak, mask_pack, unmask_text

PACK = {
    "case_id": "CASE-001",
    "case_type": "ring",
    "findings": [
        {
            "evidence_key": "E1",
            "rule": "self_referral",
            "severity": "high",
            "reason": "Dr. Anil Kumar refers members to Sunrise Diagnostics, owned by his relative",
            "claim_ids": ["CLM-1042", "CLM-1043"],
            "amount_inr": 42000,
        },
        {
            "evidence_key": "E2",
            "rule": "phantom",
            "severity": "critical",
            "reason": "Member Ravi S. billed at Sunrise Diagnostics while admitted; contact 9876543210",
            "member_name": "Ravi S.",
            "age": 54,
            "city": "Chennai",
            "claim_ids": ["CLM-2001"],
        },
    ],
    "unexpected_new_field": "should be dropped by the allowlist",
}

PEOPLE = ["Dr. Anil Kumar", "Ravi S."]
ORGS = ["Sunrise Diagnostics"]


def test_names_and_fields_are_masked():
    masked, vault = mask_pack(PACK, PEOPLE, ORGS)
    text = str(masked)
    for secret in ["Anil Kumar", "Ravi S.", "Sunrise Diagnostics", "9876543210", "Chennai"]:
        assert secret not in text
    assert "PERSON_1" in text and "ORG_1" in text and "PHONE_1" in text
    assert "age" not in masked["findings"][1]
    assert "unexpected_new_field" not in masked
    assert_no_leak(masked, vault)


def test_ids_and_numbers_are_kept():
    masked, _ = mask_pack(PACK, PEOPLE, ORGS)
    assert masked["findings"][0]["claim_ids"] == ["CLM-1042", "CLM-1043"]
    assert masked["findings"][0]["amount_inr"] == 42000


def test_same_name_gets_same_token():
    masked, _ = mask_pack(PACK, PEOPLE, ORGS)
    assert "ORG_1" in masked["findings"][0]["reason"]
    assert "ORG_1" in masked["findings"][1]["reason"]


def test_unmask_restores_names_locally():
    _, vault = mask_pack(PACK, PEOPLE, ORGS)
    reply = "PERSON_1 referred members to ORG_1 [E1]."
    assert unmask_text(reply, vault) == "Dr. Anil Kumar referred members to Sunrise Diagnostics [E1]."


def test_leak_check_blocks_unmasked_payload():
    _, vault = mask_pack(PACK, PEOPLE, ORGS)
    with pytest.raises(LeakError):
        assert_no_leak({"reason": "Dr. Anil Kumar billed twice"}, vault)
