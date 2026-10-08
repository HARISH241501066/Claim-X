"""Rule engine: auto-loads every module in rules/, runs each rule safely, saves findings."""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import pkgutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from backend.detect.base import Finding, Rule
from backend.detect.context import Context, load_tables

log = logging.getLogger("claimshield.detect")
RULES_PACKAGE = "backend.detect.rules"
DB_PATH = Path(__file__).resolve().parents[1] / "claimshield.db"

FINDINGS_DDL = """
CREATE TABLE findings (
    finding_id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, detector TEXT NOT NULL,
    score REAL NOT NULL, severity TEXT NOT NULL, reason TEXT NOT NULL,
    evidence_ids TEXT NOT NULL);
"""


@dataclass
class RunResult:
    findings: list[Finding] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # rule name -> error
    rules_run: list[str] = field(default_factory=list)


def load_rules(package: str = RULES_PACKAGE) -> list[Rule]:
    """Import every non-private module in the package and instantiate its Rule subclasses."""
    importlib.invalidate_caches()
    pkg = importlib.import_module(package)
    rules: list[Rule] = []
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{package}.{info.name}")
        except Exception:
            log.exception("skipping rule module %s: import failed", info.name)
            continue
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(cls, Rule)
                and cls.__module__ == module.__name__
                and not inspect.isabstract(cls)
            ):
                try:
                    rules.append(cls())
                except Exception:
                    log.exception("skipping rule %s: could not instantiate", cls.__name__)
    return sorted(rules, key=lambda r: r.name)


def run_rules(claims: pd.DataFrame, ctx: Context, rules: list[Rule] | None = None) -> RunResult:
    result = RunResult()
    for rule in load_rules() if rules is None else rules:
        try:
            found = rule.evaluate(claims, ctx)
            bad = [f for f in found if not isinstance(f, Finding)]
            if bad:
                raise TypeError(f"rule returned {len(bad)} non-Finding item(s)")
        except Exception as exc:
            log.exception("rule %s failed and was skipped", rule.name)
            result.errors[rule.name] = repr(exc)
            continue
        result.rules_run.append(rule.name)
        result.findings.extend(found)
    result.findings.sort(key=lambda f: (f.detector, -f.score, f.entity_id, f.evidence_ids))
    return result


def save_findings(db_path: str | Path, findings: list[Finding]) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute("DROP TABLE IF EXISTS findings")
        con.executescript(FINDINGS_DDL)
        con.executemany(
            "INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    f"FND-{i:06d}", f.entity_id, f.detector, f.score, f.severity, f.reason,
                    json.dumps(f.evidence_ids),
                )
                for i, f in enumerate(findings, 1)
            ],
        )
        con.commit()
    finally:
        con.close()


def load_findings(db_path: str | Path) -> list[dict]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT finding_id, entity_id, detector, score, severity, reason, evidence_ids "
            "FROM findings ORDER BY finding_id"
        ).fetchall()
    finally:
        con.close()
    keys = ["finding_id", "entity_id", "detector", "score", "severity", "reason", "evidence_ids"]
    out = [dict(zip(keys, r, strict=True)) for r in rows]
    for row in out:
        row["evidence_ids"] = json.loads(row["evidence_ids"])
    return out


def run(db_path: str | Path = DB_PATH) -> RunResult:
    tables = load_tables(db_path)
    ctx = Context.build(tables)
    result = run_rules(tables["claims"], ctx)
    save_findings(db_path, result.findings)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    res = run()
    print(f"rules run: {', '.join(res.rules_run)}")
    if res.errors:
        print(f"rules skipped: {res.errors}")
    counts = pd.Series([f.detector for f in res.findings]).value_counts()
    print(counts.to_string())
    print(f"total findings: {len(res.findings)}")
