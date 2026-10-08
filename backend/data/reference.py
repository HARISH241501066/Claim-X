"""Static reference data for the synthetic generator (all prices in rupees)."""

# city -> (latitude, longitude)
CITIES: dict[str, tuple[float, float]] = {
    "Chennai": (13.0827, 80.2707),
    "Mumbai": (19.0760, 72.8777),
    "Delhi": (28.6139, 77.2090),
    "Bengaluru": (12.9716, 77.5946),
    "Hyderabad": (17.3850, 78.4867),
    "Kolkata": (22.5726, 88.3639),
}

# specialty -> (code abbreviation, price multiplier) for levelled consultation codes
CONSULT: dict[str, tuple[str, float]] = {
    "General Medicine": ("GM", 1.0),
    "Cardiology": ("CARD", 1.8),
    "Orthopedics": ("ORTH", 1.5),
    "Dermatology": ("DERM", 1.2),
    "Pediatrics": ("PED", 1.0),
}

SPECIALTIES: list[str] = [*CONSULT, "Physiotherapy", "Pathology", "Radiology"]

# base price of a consultation at each code level (1 = brief ... 5 = most complex)
LEVEL_BASE_PRICE: dict[int, int] = {1: 300, 2: 600, 3: 1000, 4: 1600, 5: 2500}

PANEL_CODE = "PNL-BMP"
PANEL_COMPONENTS: list[str] = ["LAB-GLU", "LAB-UREA", "LAB-CREAT", "LAB-NA"]

# (code, description, specialty, price) for codes that carry no level
_FLAT_CODES: list[tuple[str, str, str, int]] = [
    ("PHY-SESSION", "Physiotherapy session", "Physiotherapy", 800),
    ("LAB-CBC", "Complete blood count", "Pathology", 350),
    ("LAB-GLU", "Glucose (component)", "Pathology", 250),
    ("LAB-UREA", "Urea (component)", "Pathology", 300),
    ("LAB-CREAT", "Creatinine (component)", "Pathology", 300),
    ("LAB-NA", "Sodium (component)", "Pathology", 350),
    (PANEL_CODE, "Basic metabolic panel (bundles the 4 components)", "Pathology", 900),
    ("RAD-XRAY", "X-ray", "Radiology", 700),
    ("RAD-USG", "Ultrasound", "Radiology", 1200),
    ("RAD-MRI", "MRI scan", "Radiology", 7500),
    ("INP-DAY", "Inpatient day", "General Medicine", 6000),
    ("PHM-RX", "Pharmacy dispensing", "Pharmacy", 450),
    ("DME-WC", "Wheelchair", "DME", 6500),
    ("DME-BP", "Blood pressure monitor", "DME", 2200),
]


def consult_code(specialty: str, level: int) -> str:
    return f"CON-{CONSULT[specialty][0]}-{level}"


def _build_procedure_codes() -> list[dict]:
    rows: list[dict] = []
    for specialty, (abbr, multiplier) in CONSULT.items():
        for level, base in LEVEL_BASE_PRICE.items():
            rows.append(
                {
                    "code": f"CON-{abbr}-{level}",
                    "description": f"{specialty} consultation, level {level}",
                    "specialty": specialty,
                    "code_level": level,
                    "price_inr": round(base * multiplier),
                }
            )
    for code, description, specialty, price in _FLAT_CODES:
        rows.append(
            {
                "code": code,
                "description": description,
                "specialty": specialty,
                "code_level": None,
                "price_inr": price,
            }
        )
    return rows


PROCEDURE_CODES: list[dict] = _build_procedure_codes()
PRICE: dict[str, int] = {r["code"]: r["price_inr"] for r in PROCEDURE_CODES}
CODE_LEVEL: dict[str, int | None] = {r["code"]: r["code_level"] for r in PROCEDURE_CODES}
