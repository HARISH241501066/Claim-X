"""Synthetic data generator for ClaimShield Nexus (synthetic data only, seed 42).

Normal behaviour is generated first, then one inject_* function per planted scenario.
Outputs: a SQLite database and ground_truth.csv (entity_id, scenario). The ground truth is
for tests only; it is never written to the database and no detector may read it.

Run from the repo root:  python -m backend.data.generator
"""

from __future__ import annotations

import calendar
import csv
import math
import random
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from backend.data import reference as ref

SEED = 42
START = date(2026, 1, 1)
END = date(2026, 6, 30)
WINDOW = (START, END)
DB_PATH = Path(__file__).resolve().parents[1] / "claimshield.db"
TRUTH_PATH = Path(__file__).resolve().parents[1] / "ground_truth.csv"

TARGETS = {
    "providers": 40,
    "facilities": 15,
    "owners": 20,
    "members": 300,
    "claims": 5000,
    "inpatient_stays": 60,
    "referrals": 600,
    "investigations": 25,
}

N_VISIT_POOL = 3600  # normal visit claims before injection; surplus is trimmed to hit the target
N_NORMAL_REFERRALS = 420
RING_ID, RING_LAB, RING_CLINIC = "PRV-A01", "FAC-B01", "FAC-C01"
UPCODER, PHANTOM_PROV, OFFENDER, HONEST = "PRV-005", "PRV-007", "PRV-010", "PRV-015"
PHYSIO_PROV, UNBUNDLERS = "PRV-019", ("PRV-024", "PRV-025")
SPECIAL_PROVIDERS = {RING_ID, UPCODER, OFFENDER, HONEST}
NORMAL_LEVEL_WEIGHTS = [0.15, 0.30, 0.35, 0.15, 0.05]

MEMBERS_PER_CITY = {
    "Chennai": 80,
    "Mumbai": 55,
    "Delhi": 50,
    "Bengaluru": 45,
    "Hyderabad": 40,
    "Kolkata": 30,
}
# provider i (PRV-001..PRV-039) gets SPEC_SEQUENCE[i-1]; PRV-A01 is General Medicine
SPEC_SEQUENCE = (
    ["General Medicine"] * 8
    + ["Orthopedics"] * 5
    + ["Cardiology"] * 5
    + ["Physiotherapy"] * 5
    + ["Pathology"] * 5
    + ["Radiology"] * 3
    + ["Dermatology"] * 4
    + ["Pediatrics"] * 4
)
FIXED_CITY = {
    RING_ID: "Chennai",
    UPCODER: "Mumbai",
    PHANTOM_PROV: "Delhi",
    OFFENDER: "Bengaluru",
    HONEST: "Hyderabad",
    PHYSIO_PROV: "Mumbai",
    UNBUNDLERS[0]: "Delhi",
    UNBUNDLERS[1]: "Bengaluru",
}


@dataclass
class World:
    owners: list[dict] = field(default_factory=list)
    facilities: list[dict] = field(default_factory=list)
    providers: list[dict] = field(default_factory=list)
    members: list[dict] = field(default_factory=list)
    stays: list[dict] = field(default_factory=list)
    referrals: list[dict] = field(default_factory=list)
    investigations: list[dict] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    truth: list[tuple[str, str]] = field(default_factory=list)
    prov: dict[str, dict] = field(default_factory=dict)
    fac: dict[str, dict] = field(default_factory=dict)
    member_city: dict[str, str] = field(default_factory=dict)
    members_by_city: dict[str, list[str]] = field(default_factory=dict)
    stays_by_member: dict[str, list[tuple[date, date]]] = field(default_factory=dict)
    normal_providers: list[dict] = field(default_factory=list)
    ring_members: list[str] = field(default_factory=list)
    overutil_members: list[str] = field(default_factory=list)
    keys: set = field(default_factory=set)
    seq: int = 0


# ---------------------------------------------------------------- helpers


def rand_date(rng: random.Random, lo: date = START, hi: date = END) -> date:
    return lo + timedelta(days=rng.randint(0, (hi - lo).days))


def noisy(rng: random.Random, price: int) -> int:
    return round(price * rng.uniform(0.97, 1.03))


def in_stay(w: World, member_id: str, d: date) -> bool:
    return any(a <= d <= b for a, b in w.stays_by_member.get(member_id, []))


def free_date(
    w: World, rng: random.Random, member_id: str, lo: date = START, hi: date = END
) -> date:
    for _ in range(200):
        d = rand_date(rng, lo, hi)
        if not in_stay(w, member_id, d):
            return d
    raise RuntimeError(f"no stay-free date for {member_id}")


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(
        (lon2 - lon1) / 2
    ) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _free_slot(w: World, member_id: str, provider_id: str, code: str, d: date) -> date:
    """First date at or after d (else before) not already used by this member/provider/code."""
    for step in (1, -1):
        cand = d
        while WINDOW[0] <= cand <= WINDOW[1]:
            taken = (member_id, provider_id, code, cand) in w.keys
            if not taken and (cand == d or not in_stay(w, member_id, cand)):
                return cand
            cand += timedelta(days=step)
    raise RuntimeError(f"no free date for {member_id}/{provider_id}/{code}")


def add_claim(
    w: World,
    *,
    member_id: str,
    provider_id: str,
    facility_id: str,
    service_date: date,
    code: str,
    billed: int,
    referring: str | None = None,
    claim_type: str = "outpatient",
    kind: str = "normal_visit",
    tag: str | None = None,
) -> dict:
    """Append a claim; a clash on member, provider, code and date shifts the service date."""
    service_date = _free_slot(w, member_id, provider_id, code, service_date)
    w.keys.add((member_id, provider_id, code, service_date))
    w.seq += 1
    claim = {
        "member_id": member_id,
        "provider_id": provider_id,
        "facility_id": facility_id,
        "referring_provider_id": referring,
        "service_date": service_date.isoformat(),
        "procedure_code": code,
        "code_level": ref.CODE_LEVEL[code],
        "billed_amount": billed,
        "claim_type": claim_type,
        "_kind": kind,
        "_tag": tag,
        "_seq": w.seq,
        "_protected": False,
    }
    w.claims.append(claim)
    return claim


def add_referral(w: World, member_id: str, provider_id: str, facility_id: str, d: date) -> None:
    w.referrals.append(
        {
            "member_id": member_id,
            "from_provider_id": provider_id,
            "to_facility_id": facility_id,
            "referral_date": d.isoformat(),
        }
    )


# ---------------------------------------------------------------- normal behaviour


def build_entities(w: World, rng: random.Random) -> None:
    city_names = list(ref.CITIES)

    for i in range(1, 21):
        w.owners.append({"owner_id": f"OWN-{i:03d}", "related_to": None})
    w.owners[0]["related_to"] = "OWN-002"
    w.owners[1]["related_to"] = "OWN-001"

    other_owners = rng.sample([f"OWN-{i:03d}" for i in range(3, 21)], 13)
    plan = (
        [("clinic", c) for c in city_names]
        + [("lab", "Mumbai"), ("lab", "Delhi"), ("lab", "Bengaluru")]
        + [("pharmacy", "Chennai"), ("pharmacy", "Mumbai")]
        + [("DME", "Delhi"), ("DME", "Hyderabad")]
    )
    specs = [
        (RING_LAB, "lab", "Chennai", "OWN-001"),
        (RING_CLINIC, "clinic", "Chennai", "OWN-002"),
    ]
    for i, ((ftype, city), owner) in enumerate(zip(plan, other_owners, strict=True), 1):
        specs.append((f"FAC-{i:03d}", ftype, city, owner))
    for fid, ftype, city, owner in specs:
        lat, lon = ref.CITIES[city]
        w.facilities.append(
            {
                "facility_id": fid,
                "type": ftype,
                "city": city,
                "lat": round(lat + rng.uniform(-0.05, 0.05), 5),
                "lon": round(lon + rng.uniform(-0.05, 0.05), 5),
                "owner_id": owner,
            }
        )
    w.fac = {f["facility_id"]: f for f in w.facilities}

    normal_facs = [f for f in w.facilities if f["facility_id"] not in (RING_LAB, RING_CLINIC)]
    clinic_by_city = {f["city"]: f["facility_id"] for f in normal_facs if f["type"] == "clinic"}
    labs = [f for f in normal_facs if f["type"] == "lab"]

    weights = list(MEMBERS_PER_CITY.values())
    prov_ids = [RING_ID] + [f"PRV-{i:03d}" for i in range(1, 40)]
    specialties = ["General Medicine", *SPEC_SEQUENCE]
    for pid, spec in zip(prov_ids, specialties, strict=True):
        city = FIXED_CITY.get(pid) or rng.choices(city_names, weights)[0]
        if spec == "Pathology":
            origin = ref.CITIES[city]
            lab = min(labs, key=lambda f: haversine_km(origin, ref.CITIES[f["city"]]))
            facility_id = lab["facility_id"]
        else:
            facility_id = clinic_by_city[city]
        joined = date(2010, 1, 1) + timedelta(days=rng.randint(0, 5400))
        w.providers.append(
            {
                "provider_id": pid,
                "specialty": spec,
                "city": city,
                "facility_id": facility_id,
                "joined_date": joined.isoformat(),
            }
        )
    w.prov = {p["provider_id"]: p for p in w.providers}
    w.normal_providers = [p for p in w.providers if p["provider_id"] not in SPECIAL_PROVIDERS]

    cities = [c for c, n in MEMBERS_PER_CITY.items() for _ in range(n)]
    rng.shuffle(cities)
    for i, city in enumerate(cities, 1):
        w.members.append(
            {
                "member_id": f"MEM-{i:03d}",
                "age": rng.randint(1, 90),
                "gender": rng.choice(["F", "M"]),
                "city": city,
            }
        )
    for m in w.members:
        w.member_city[m["member_id"]] = m["city"]
        w.members_by_city.setdefault(m["city"], []).append(m["member_id"])
    w.ring_members = rng.sample(w.members_by_city["Chennai"], 15)
    w.overutil_members = rng.sample(w.members_by_city[w.prov[PHYSIO_PROV]["city"]], 3)


def build_stays(w: World, rng: random.Random) -> None:
    reserved = set(w.ring_members) | set(w.overutil_members)
    eligible = [m["member_id"] for m in w.members if m["member_id"] not in reserved]
    clinic_by_city = {
        f["city"]: f["facility_id"]
        for f in w.facilities
        if f["type"] == "clinic" and f["facility_id"] != RING_CLINIC
    }
    stays = []
    for mid in rng.sample(eligible, 60):
        admit = rand_date(rng, START, END - timedelta(days=9))
        discharge = admit + timedelta(days=rng.randint(2, 8))
        stays.append((admit, discharge, mid))
    stays.sort()
    for i, (admit, discharge, mid) in enumerate(stays, 1):
        w.stays.append(
            {
                "stay_id": f"STAY-{i:03d}",
                "member_id": mid,
                "facility_id": clinic_by_city[w.member_city[mid]],
                "admit_date": admit.isoformat(),
                "discharge_date": discharge.isoformat(),
            }
        )
        w.stays_by_member.setdefault(mid, []).append((admit, discharge))


def _visit_code(rng: random.Random, specialty: str) -> str:
    if specialty in ref.CONSULT:
        level = rng.choices([1, 2, 3, 4, 5], NORMAL_LEVEL_WEIGHTS)[0]
        return ref.consult_code(specialty, level)
    if specialty == "Physiotherapy":
        return "PHY-SESSION"
    if specialty == "Pathology":
        return rng.choice(["LAB-CBC", "PNL-BMP"])
    return rng.choices(["RAD-XRAY", "RAD-USG", "RAD-MRI"], [5, 4, 1])[0]


def build_normal_claims(w: World, rng: random.Random) -> None:
    # inpatient claims, one per stay
    consult_provs = [p for p in w.normal_providers if p["specialty"] in ref.CONSULT]
    for stay in w.stays:
        admit = date.fromisoformat(stay["admit_date"])
        discharge = date.fromisoformat(stay["discharge_date"])
        city = w.member_city[stay["member_id"]]
        local = [p for p in consult_provs if p["city"] == city] or consult_provs
        days = (discharge - admit).days + 1
        add_claim(
            w,
            member_id=stay["member_id"],
            provider_id=rng.choice(local)["provider_id"],
            facility_id=stay["facility_id"],
            service_date=discharge,
            code="INP-DAY",
            billed=noisy(rng, ref.PRICE["INP-DAY"] * days),
            claim_type="inpatient",
            kind="inpatient",
        )

    # outpatient visits
    physio_count: dict[tuple[str, int], int] = defaultdict(int)
    made = 0
    while made < N_VISIT_POOL:
        prov = rng.choice(w.normal_providers)
        member = rng.choice(w.members_by_city[prov["city"]])
        d = free_date(w, rng, member)
        if prov["specialty"] == "Physiotherapy":
            if member in w.overutil_members or physio_count[(member, d.month)] >= 3:
                continue
            physio_count[(member, d.month)] += 1
        code = _visit_code(rng, prov["specialty"])
        add_claim(
            w,
            member_id=member,
            provider_id=prov["provider_id"],
            facility_id=prov["facility_id"],
            service_date=d,
            code=code,
            billed=noisy(rng, ref.PRICE[code]),
        )
        made += 1

    # referral-linked facility claims
    other_facs = [
        f for f in w.facilities if f["facility_id"] not in (RING_LAB, RING_CLINIC)
    ]
    codes_by_type = {
        "lab": ["LAB-CBC", "PNL-BMP"],
        "clinic": ["RAD-XRAY", "RAD-USG"],
        "pharmacy": ["PHM-RX"],
        "DME": ["DME-WC", "DME-BP"],
    }
    ring = set(w.ring_members)
    made_referrals = 0
    while made_referrals < N_NORMAL_REFERRALS:
        prov = rng.choice(consult_provs)
        member = rng.choice([m for m in w.members_by_city[prov["city"]] if m not in ring])
        if prov["city"] == "Chennai" and rng.random() < 0.08:
            fac = w.fac[rng.choice([RING_LAB, RING_CLINIC])]
        else:  # members are referred within their own city
            fac = rng.choice([f for f in other_facs if f["city"] == prov["city"]])
        ref_date = rand_date(rng, START, END - timedelta(days=7))
        try:
            claim_date = free_date(w, rng, member, ref_date, ref_date + timedelta(days=7))
        except RuntimeError:  # window falls wholly inside the member's stay; draw again
            continue
        code = rng.choice(codes_by_type[fac["type"]])
        add_claim(
            w,
            member_id=member,
            provider_id=prov["provider_id"],
            facility_id=fac["facility_id"],
            service_date=claim_date,
            code=code,
            billed=noisy(rng, ref.PRICE[code]),
            referring=prov["provider_id"],
            kind="normal_referral",
        )
        add_referral(w, member, prov["provider_id"], fac["facility_id"], ref_date)
        made_referrals += 1


# ---------------------------------------------------------------- planted scenarios


def inject_ring(w: World, rng: random.Random) -> None:
    """PRV-A01 refers 15 members monthly to FAC-B01 and FAC-C01 (related owners), inflated."""
    a01 = w.prov[RING_ID]
    for member in w.ring_members:
        for month in range(1, 7):
            first = date(2026, month, 1)
            last = date(2026, month, calendar.monthrange(2026, month)[1])
            d = first + timedelta(days=rng.randint(0, (last - first).days - 6))
            code = ref.consult_code("General Medicine", rng.choice([3, 4]))
            add_claim(
                w,
                member_id=member,
                provider_id=RING_ID,
                facility_id=a01["facility_id"],
                service_date=d,
                code=code,
                billed=noisy(rng, ref.PRICE[code]),
                kind="ring",
            )
            for fac_id, codes, max_lag in (
                (RING_LAB, ["PNL-BMP", "LAB-CBC"], 3),
                (RING_CLINIC, ["RAD-USG", "RAD-XRAY"], 5),
            ):
                add_referral(w, member, RING_ID, fac_id, d)
                code = rng.choice(codes)
                add_claim(
                    w,
                    member_id=member,
                    provider_id=RING_ID,
                    facility_id=fac_id,
                    service_date=d + timedelta(days=rng.randint(1, max_lag)),
                    code=code,
                    billed=round(ref.PRICE[code] * rng.uniform(2.3, 2.7)),
                    referring=RING_ID,
                    kind="ring",
                )
    for entity in (RING_ID, RING_LAB, RING_CLINIC, "OWN-001", "OWN-002"):
        w.truth.append((entity, "ring"))


def inject_upcoder(w: World, rng: random.Random) -> None:
    """One provider bills level 5 on 70% of visits."""
    prov = w.prov[UPCODER]
    n, n5 = 120, 84
    levels = [5] * n5 + [rng.choice([1, 2, 3]) for _ in range(n - n5)]
    rng.shuffle(levels)
    for level in levels:
        member = rng.choice(w.members_by_city[prov["city"]])
        code = ref.consult_code(prov["specialty"], level)
        add_claim(
            w,
            member_id=member,
            provider_id=UPCODER,
            facility_id=prov["facility_id"],
            service_date=free_date(w, rng, member),
            code=code,
            billed=noisy(rng, ref.PRICE[code]),
            kind="upcoder",
        )
    w.truth.append((UPCODER, "upcoder"))


def inject_double_billing(w: World, rng: random.Random) -> None:
    """20 exact duplicate claims (identical except claim_id)."""
    candidates = [c for c in w.claims if c["_kind"] == "normal_visit"]
    for original in rng.sample(candidates, 20):
        original["_protected"] = True
        w.seq += 1
        dup = dict(original)
        dup.update(_kind="double_billing", _tag="double_billing", _seq=w.seq, _protected=True)
        w.claims.append(dup)


def inject_unbundling(w: World, rng: random.Random) -> None:
    """Component codes billed on the same day instead of the panel code (12 events)."""
    for pid in UNBUNDLERS:
        prov = w.prov[pid]
        used: set[tuple[str, date]] = set()
        for _ in range(6):
            while True:
                member = rng.choice(w.members_by_city[prov["city"]])
                d = free_date(w, rng, member)
                if (member, d) not in used:
                    break
            used.add((member, d))
            for code in ref.PANEL_COMPONENTS:
                add_claim(
                    w,
                    member_id=member,
                    provider_id=pid,
                    facility_id=prov["facility_id"],
                    service_date=d,
                    code=code,
                    billed=noisy(rng, ref.PRICE[code]),
                    kind="unbundling",
                    tag="unbundling",
                )
        w.truth.append((pid, "unbundling"))


def inject_phantom(w: World, rng: random.Random) -> None:
    """Outpatient claims dated inside members' inpatient stays at another facility/city."""
    prov = w.prov[PHANTOM_PROV]
    eligible = [s for s in w.stays if w.member_city[s["member_id"]] != prov["city"]]
    for stay in rng.sample(eligible, 10):
        admit = date.fromisoformat(stay["admit_date"])
        discharge = date.fromisoformat(stay["discharge_date"])
        d = admit + timedelta(days=rng.randint(0, (discharge - admit).days))
        code = ref.consult_code("General Medicine", rng.choice([2, 3]))
        add_claim(
            w,
            member_id=stay["member_id"],
            provider_id=PHANTOM_PROV,
            facility_id=prov["facility_id"],
            service_date=d,
            code=code,
            billed=noisy(rng, ref.PRICE[code]),
            kind="phantom",
            tag="phantom",
        )
    w.truth.append((PHANTOM_PROV, "phantom"))


def inject_overutilizer(w: World, rng: random.Random) -> None:
    """~25 physiotherapy sessions per member per month for 3 months."""
    prov = w.prov[PHYSIO_PROV]
    for member in w.overutil_members:
        for month in (4, 5, 6):
            days = rng.sample(range(1, calendar.monthrange(2026, month)[1] + 1), 25)
            for day in days:
                add_claim(
                    w,
                    member_id=member,
                    provider_id=PHYSIO_PROV,
                    facility_id=prov["facility_id"],
                    service_date=date(2026, month, day),
                    code="PHY-SESSION",
                    billed=noisy(rng, ref.PRICE["PHY-SESSION"]),
                    kind="overutilizer",
                )
        w.truth.append((member, "overutilizer"))


def inject_repeat_offender(w: World, rng: random.Random) -> None:
    """Past confirmed investigation (see build_investigations) and rising recent volume."""
    prov = w.prov[OFFENDER]
    for month, count in enumerate([14, 15, 16, 18, 30, 45], 1):
        first = date(2026, month, 1)
        last = date(2026, month, calendar.monthrange(2026, month)[1])
        for _ in range(count):
            member = rng.choice(w.members_by_city[prov["city"]])
            code = _visit_code(rng, prov["specialty"])
            add_claim(
                w,
                member_id=member,
                provider_id=OFFENDER,
                facility_id=prov["facility_id"],
                service_date=free_date(w, rng, member, first, last),
                code=code,
                billed=noisy(rng, ref.PRICE[code]),
                kind="repeat_offender",
            )
    w.truth.append((OFFENDER, "repeat_offender"))


def inject_honest_cases(w: World, rng: random.Random) -> None:
    """A busy honest specialist and genuine same-day follow-ups (different code, same day)."""
    prov = w.prov[HONEST]
    for _ in range(200):
        member = rng.choice(w.members_by_city[prov["city"]])
        level = rng.choices([1, 2, 3, 4, 5], [0.05, 0.15, 0.35, 0.30, 0.15])[0]
        code = ref.consult_code(prov["specialty"], level)
        add_claim(
            w,
            member_id=member,
            provider_id=HONEST,
            facility_id=prov["facility_id"],
            service_date=free_date(w, rng, member),
            code=code,
            billed=noisy(rng, ref.PRICE[code]),
            kind="honest",
        )
    w.truth.append((HONEST, "honest_specialist"))

    consult_provs = [p for p in w.normal_providers if p["specialty"] in ref.CONSULT]
    for _ in range(20):
        p = rng.choice(consult_provs)
        member = rng.choice(w.members_by_city[p["city"]])
        d = free_date(w, rng, member)
        for level in (3, 1):  # initial consult, then a brief same-day follow-up
            code = ref.consult_code(p["specialty"], level)
            add_claim(
                w,
                member_id=member,
                provider_id=p["provider_id"],
                facility_id=p["facility_id"],
                service_date=d,
                code=code,
                billed=noisy(rng, ref.PRICE[code]),
                kind="honest_followup",
                tag="honest_followup",
            )


# ---------------------------------------------------------------- investigations / finalize


def build_investigations(w: World, rng: random.Random) -> None:
    rows: list[tuple[str, str, date, date, str]] = [
        (OFFENDER, "provider", date(2025, 6, 2), date(2025, 9, 15), "confirmed"),
        (HONEST, "provider", date(2025, 10, 6), date(2025, 11, 21), "cleared"),
    ]
    excluded = {RING_ID, UPCODER, PHANTOM_PROV, OFFENDER, HONEST, PHYSIO_PROV, *UNBUNDLERS}
    provider_pool = [p["provider_id"] for p in w.providers if p["provider_id"] not in excluded]
    facility_pool = [
        f["facility_id"] for f in w.facilities if f["facility_id"] not in (RING_LAB, RING_CLINIC)
    ]
    entities = [(e, "provider") for e in rng.sample(provider_pool, 20)]
    entities += [(e, "facility") for e in rng.sample(facility_pool, 3)]
    outcomes = ["confirmed"] * 9 + ["cleared"] * 14
    rng.shuffle(outcomes)
    lo, hi = date(2025, 7, 1), date(2026, 4, 30)
    for (entity, etype), outcome in zip(entities, outcomes, strict=True):
        opened = rand_date(rng, lo, hi)
        rows.append((entity, etype, opened, opened + timedelta(days=rng.randint(14, 75)), outcome))
    rows.sort(key=lambda r: (r[2], r[0]))
    for i, (entity, etype, opened, closed, outcome) in enumerate(rows, 1):
        w.investigations.append(
            {
                "case_id": f"CASE-{i:03d}",
                "entity_id": entity,
                "entity_type": etype,
                "opened_date": opened.isoformat(),
                "closed_date": closed.isoformat(),
                "outcome": outcome,
            }
        )


def finalize(w: World, rng: random.Random) -> None:
    """Trim surplus normal visits to hit the claim target, then assign IDs."""
    excess = len(w.claims) - TARGETS["claims"]
    removable = [c for c in w.claims if c["_kind"] == "normal_visit" and not c["_protected"]]
    if excess < 0 or excess > len(removable):
        raise RuntimeError(f"cannot reach claim target (excess={excess})")
    dropped = {id(c) for c in rng.sample(removable, excess)}
    w.claims = [c for c in w.claims if id(c) not in dropped]
    w.claims.sort(key=lambda c: (c["service_date"], c["_seq"]))
    for i, c in enumerate(w.claims, 1):
        c["claim_id"] = f"CLM-{i:06d}"
        if c["_tag"]:
            w.truth.append((c["claim_id"], c["_tag"]))
    w.referrals.sort(key=lambda r: (r["referral_date"], r["member_id"], r["to_facility_id"]))
    for i, r in enumerate(w.referrals, 1):
        r["referral_id"] = f"REF-{i:06d}"


# ---------------------------------------------------------------- output

DDL = """
CREATE TABLE owners (owner_id TEXT PRIMARY KEY, related_to TEXT REFERENCES owners(owner_id));
CREATE TABLE facilities (
    facility_id TEXT PRIMARY KEY, type TEXT NOT NULL, city TEXT NOT NULL,
    lat REAL NOT NULL, lon REAL NOT NULL, owner_id TEXT NOT NULL REFERENCES owners(owner_id));
CREATE TABLE providers (
    provider_id TEXT PRIMARY KEY, specialty TEXT NOT NULL, city TEXT NOT NULL,
    facility_id TEXT REFERENCES facilities(facility_id), joined_date TEXT NOT NULL);
CREATE TABLE members (
    member_id TEXT PRIMARY KEY, age INTEGER NOT NULL, gender TEXT NOT NULL, city TEXT NOT NULL);
CREATE TABLE procedure_codes (
    code TEXT PRIMARY KEY, description TEXT NOT NULL, specialty TEXT NOT NULL,
    code_level INTEGER, price_inr INTEGER NOT NULL);
CREATE TABLE panel_components (
    panel_code TEXT NOT NULL REFERENCES procedure_codes(code),
    component_code TEXT NOT NULL REFERENCES procedure_codes(code),
    PRIMARY KEY (panel_code, component_code));
CREATE TABLE cities (city TEXT PRIMARY KEY, lat REAL NOT NULL, lon REAL NOT NULL);
CREATE TABLE claims (
    claim_id TEXT PRIMARY KEY, member_id TEXT NOT NULL REFERENCES members(member_id),
    provider_id TEXT NOT NULL REFERENCES providers(provider_id),
    facility_id TEXT NOT NULL REFERENCES facilities(facility_id),
    referring_provider_id TEXT REFERENCES providers(provider_id),
    service_date TEXT NOT NULL, procedure_code TEXT NOT NULL REFERENCES procedure_codes(code),
    code_level INTEGER, billed_amount INTEGER NOT NULL, claim_type TEXT NOT NULL);
CREATE TABLE inpatient_stays (
    stay_id TEXT PRIMARY KEY, member_id TEXT NOT NULL REFERENCES members(member_id),
    facility_id TEXT NOT NULL REFERENCES facilities(facility_id),
    admit_date TEXT NOT NULL, discharge_date TEXT NOT NULL);
CREATE TABLE referrals (
    referral_id TEXT PRIMARY KEY, member_id TEXT NOT NULL REFERENCES members(member_id),
    from_provider_id TEXT NOT NULL REFERENCES providers(provider_id),
    to_facility_id TEXT NOT NULL REFERENCES facilities(facility_id),
    referral_date TEXT NOT NULL);
CREATE TABLE investigations (
    case_id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, entity_type TEXT NOT NULL,
    opened_date TEXT NOT NULL, closed_date TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('confirmed', 'cleared')));
"""


def _insert(con: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    cols = [c for c in rows[0] if not c.startswith("_")]
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})"
    con.executemany(sql, rows)


def write_db(w: World, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.executescript(DDL)
        _insert(con, "owners", w.owners)
        _insert(con, "facilities", w.facilities)
        _insert(con, "providers", w.providers)
        _insert(con, "members", w.members)
        _insert(con, "procedure_codes", ref.PROCEDURE_CODES)
        _insert(
            con,
            "panel_components",
            [{"panel_code": ref.PANEL_CODE, "component_code": c} for c in ref.PANEL_COMPONENTS],
        )
        _insert(
            con,
            "cities",
            [{"city": c, "lat": lat, "lon": lon} for c, (lat, lon) in ref.CITIES.items()],
        )
        claim_cols = [
            "claim_id", "member_id", "provider_id", "facility_id", "referring_provider_id",
            "service_date", "procedure_code", "code_level", "billed_amount", "claim_type",
        ]  # fmt: skip
        _insert(con, "claims", [{k: c[k] for k in claim_cols} for c in w.claims])
        _insert(con, "inpatient_stays", w.stays)
        _insert(con, "referrals", w.referrals)
        _insert(con, "investigations", w.investigations)
        con.commit()
    finally:
        con.close()


def write_truth(w: World, truth_path: Path) -> None:
    truth_path.parent.mkdir(parents=True, exist_ok=True)
    with truth_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["entity_id", "scenario"])
        writer.writerows(sorted(set(w.truth), key=lambda r: (r[1], r[0])))


def generate(
    db_path: Path = DB_PATH, truth_path: Path = TRUTH_PATH, seed: int = SEED
) -> dict[str, int]:
    rng = random.Random(seed)
    w = World()
    build_entities(w, rng)
    build_stays(w, rng)
    build_normal_claims(w, rng)
    inject_ring(w, rng)
    inject_upcoder(w, rng)
    inject_double_billing(w, rng)
    inject_unbundling(w, rng)
    inject_phantom(w, rng)
    inject_overutilizer(w, rng)
    inject_repeat_offender(w, rng)
    inject_honest_cases(w, rng)
    build_investigations(w, rng)
    finalize(w, rng)
    write_db(w, Path(db_path))
    write_truth(w, Path(truth_path))
    return {
        "providers": len(w.providers),
        "facilities": len(w.facilities),
        "owners": len(w.owners),
        "members": len(w.members),
        "claims": len(w.claims),
        "inpatient_stays": len(w.stays),
        "referrals": len(w.referrals),
        "investigations": len(w.investigations),
    }


if __name__ == "__main__":
    for table, n in generate().items():
        print(f"{table:16s} {n}")
