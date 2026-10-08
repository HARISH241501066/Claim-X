"""The unit report: workload, decisions, money at risk and overdue cases, as a PDF or a CSV."""

from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import UTC, datetime

from backend.api import views
from backend.api.scope import scoped_state
from backend.cases import ranking
from backend.reports.common import H1, H2, PAGE_WIDTH, cell, clean, head_row, para, render, table

PRIORITY_HIGH = 0.60  # priority at or above this reads "high"
PRIORITY_MEDIUM = 0.35  # at or above this "medium", below it "low"
OVERDUE_DAYS = 3
INVESTIGATOR_HOURS = 40.0  # one investigator's weekly capacity, used by the workload view too
CSV_COLUMNS = [
    "case_id", "title", "rank", "priority", "priority_band", "status", "assignee", "latest_decision",
    "decision_date", "decided_by", "amount_at_risk_rs", "pending_days", "overdue",
]  # fmt: skip
STATUS_ORDER = ["unassigned", "assigned", "in_review", "closed"]
BANDS = ["high", "medium", "low"]


def priority_band(priority: float) -> str:
    return "high" if priority >= PRIORITY_HIGH else "medium" if priority >= PRIORITY_MEDIUM else "low"


def _days_since(stamp: str | None, now: datetime) -> int | None:
    if not stamp:
        return None
    return max(0, (now - datetime.fromisoformat(stamp)).days)


def unit_data(rt, unit_id: int, now: datetime | None = None) -> dict:
    """One row per case of the unit, plus the per-investigator and total figures."""
    now = now or datetime.now(UTC)
    state = rt.state
    assignments = rt.access.assignments(unit_id)
    cases = [c for c in state.cases if c.case_id in assignments]
    queue = views.build_queue(scoped_state(state, cases), {}, state.team_hours,
                              ranking.Weights(), True, rt.overrides)  # fmt: skip
    items = {i.case_id: i for i in queue.scheduled + queue.backlog}
    users = {u.id: u for u in rt.access.list_users()}
    rows = []
    for case in sorted(cases, key=lambda c: items[c.case_id].rank):
        item, row = items[case.case_id], assignments[case.case_id]
        decision = rt.decisions.get(case.case_id)
        seen = rt.notify.get_seen(case.case_id)
        pending = None if decision else _days_since(seen["first_seen"] if seen else None, now)
        assignee = users.get(row["assignee_user_id"]) if row["assignee_user_id"] else None
        rows.append({
            "case_id": case.case_id, "title": item.title, "rank": item.rank,
            "priority": round(item.priority, 3), "priority_band": priority_band(item.priority),
            "status": row["status"], "assignee": assignee.display_name if assignee else "",
            "latest_decision": decision["action"] if decision else "",
            "decision_date": decision["ts"][:10] if decision else "",
            "decided_by": (decision.get("reviewer") or "") if decision else "",
            "amount_at_risk_rs": case.flagged_amount, "pending_days": pending if pending is not None else "",
            "overdue": bool(pending is not None and pending > OVERDUE_DAYS),
            "effort_hours": item.effort_hours, "assignee_id": row["assignee_user_id"],
            "high_priority": item.priority >= PRIORITY_HIGH,
        })  # fmt: skip
    people = []
    for u in rt.access.list_users(unit_id):
        if u.role != "investigator" or not u.active:
            continue
        mine = [r for r in rows if r["assignee_id"] == u.id]
        open_mine = [r for r in mine if r["status"] in {"assigned", "in_review"}]
        people.append({
            "user_id": u.id, "name": u.display_name,
            "assigned": len(open_mine),
            "in_review": sum(1 for r in mine if r["status"] == "in_review"),
            "closed": sum(1 for r in mine if r["status"] == "closed"),
            "decisions": sum(1 for r in mine if r["latest_decision"]),
            "open_high_priority": sum(1 for r in open_mine if r["high_priority"]),
            "effort_hours": float(sum(r["effort_hours"] for r in open_mine)),
            "capacity_hours": INVESTIGATOR_HOURS,
        })  # fmt: skip
    return {
        "unit": rt.access.get_unit(unit_id), "rows": rows, "people": people,
        "total_amount": sum(r["amount_at_risk_rs"] for r in rows),
        "overdue": [r for r in rows if r["overdue"]], "generated_at": now,
    }


def _csv_safe(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in {"=", "+", "-", "@"} else text  # no spreadsheet formulas


def unit_csv(data: dict) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for r in data["rows"]:
        writer.writerow([_csv_safe(clean(r[c])) if c not in {"overdue"} else str(r[c]).lower()
                         for c in CSV_COLUMNS])  # fmt: skip
    return out.getvalue()


def unit_pdf(data: dict, generated_by: str) -> bytes:
    unit, rows = data["unit"], data["rows"]
    stamp = data["generated_at"].isoformat(timespec="seconds").replace("+00:00", "Z")
    story = [para(f"Unit report: {unit['name']}", H1),
             para(f"Covers {', '.join(unit['region'])}. Generated by {generated_by} at {stamp}.")]  # fmt: skip

    story.append(para("Cases by status and priority", H2))
    count = Counter((r["status"], r["priority_band"]) for r in rows)
    grid = [head_row(["Status", "High", "Medium", "Low", "Total"])]
    for status in STATUS_ORDER:
        n = [count[(status, b)] for b in BANDS]
        grid.append([cell(status.replace("_", " ")), *[cell(v) for v in n], cell(sum(n))])
    story.append(table(grid, [PAGE_WIDTH * f for f in (0.28, 0.18, 0.18, 0.18, 0.18)]))
    story.append(para(f"Priority bands: high {PRIORITY_HIGH:g} and above, medium {PRIORITY_MEDIUM:g} to "
                      f"{PRIORITY_HIGH:g}, low below. Amount at risk across the unit: "
                      f"Rs {data['total_amount']:,}."))  # fmt: skip

    story.append(para("Assignments per investigator", H2))
    grid = [head_row(["Investigator", "Open", "In review", "Closed", "Decisions", "High priority", "Effort / capacity"])]
    for p in data["people"]:
        grid.append([cell(p["name"]), cell(p["assigned"]), cell(p["in_review"]), cell(p["closed"]),
                     cell(p["decisions"]), cell(p["open_high_priority"]),
                     cell(f"{p['effort_hours']:g} h of {p['capacity_hours']:g} h")])  # fmt: skip
    unassigned = sum(1 for r in rows if r["status"] == "unassigned")
    story.append(table(grid, [PAGE_WIDTH * f for f in (0.26, 0.09, 0.11, 0.09, 0.12, 0.13, 0.2)]))
    story.append(para(f"{unassigned} case(s) in the unit queue are not assigned yet."))

    story.append(para("Decisions made", H2))
    decided = [r for r in rows if r["latest_decision"]]
    if decided:
        grid = [head_row(["Case", "Decision", "Date", "By", "Amount at risk"])]
        for r in sorted(decided, key=lambda r: r["decision_date"]):
            grid.append([cell(r["case_id"]), cell(r["latest_decision"].replace("_", " ")),
                         cell(r["decision_date"]), cell(r["decided_by"]), cell(f"Rs {r['amount_at_risk_rs']:,}")])  # fmt: skip
        story.append(table(grid, [PAGE_WIDTH * f for f in (0.14, 0.32, 0.14, 0.2, 0.2)]))
    else:
        story.append(para("No decisions have been recorded yet."))

    story.append(para(f"Overdue cases (pending more than {OVERDUE_DAYS} days)", H2))
    if data["overdue"]:
        grid = [head_row(["Case", "Title", "Assignee", "Days pending"])]
        for r in data["overdue"]:
            grid.append([cell(r["case_id"]), cell(r["title"]), cell(r["assignee"] or "Not assigned"),
                         cell(r["pending_days"])])  # fmt: skip
        story.append(table(grid, [PAGE_WIDTH * f for f in (0.14, 0.5, 0.22, 0.14)]))
    else:
        story.append(para("None."))
    return render(story, f"Unit report {unit['name']}")
