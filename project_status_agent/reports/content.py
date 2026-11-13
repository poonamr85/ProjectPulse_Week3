"""Evidence-backed report assembly, basic checks, rendering, and approved saving."""

import copy
import hashlib
import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from project_status_agent.data.analysis import parse_date
from project_status_agent.data.store import Store, encoded
from project_status_agent.integrations.http import ServiceError


class MitigationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    proposed_action: str = Field(min_length=10, max_length=800)
    success_check: str = Field(min_length=10, max_length=500)


class ReportNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=10, max_length=1500)
    mitigations: list[MitigationProposal]


def summary_points(summary: str) -> list[str]:
    """Present existing prose as bullets without rewriting approved content."""
    points = []
    for line in summary.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line).strip()
        if line:
            points.extend(re.split(r"(?<=[.!?])\s+(?=[A-Z])", line))
    return points


def collect_evidence(
    tools: list, project: str, source: str, start: str, end: str
) -> dict:
    """Call four required analysis tools; do not allow incomplete facts to pass."""
    if parse_date(start) >= parse_date(end):
        raise ValueError("Report start must precede reporting date.")
    by_name = {tool.name: tool for tool in tools}
    evidence = {}
    for name in (
        "fetch_sprint_data",
        "detect_blockers",
        "risk_scorer",
        "trend_analysis",
    ):
        arguments = {"as_of_date": end}
        if name == "trend_analysis":
            arguments["start_date"] = start
        result = by_name[name].invoke(arguments)
        if not isinstance(result, dict):
            raise ServiceError(f"{name} failed: {result}")
        if (result["project_id"], result["source"], result["as_of_date"]) != (
            project,
            source,
            end,
        ):
            raise ValueError("Report tool returned mismatched project/source/date.")
        if result["results"].get("truncated") or result["coverage"]["missing_task_ids"]:
            raise ValueError(
                "Incomplete or truncated analysis requires attention before reporting."
            )
        evidence[name] = result
    return evidence


def report_facts(evidence: dict) -> dict:
    """Keep numeric and task-level facts entirely under Python control."""
    return {
        "sprint_progress": copy.deepcopy(evidence["fetch_sprint_data"]["results"]),
        "project_blockers": copy.deepcopy(evidence["detect_blockers"]["results"]),
        "project_risks": copy.deepcopy(evidence["risk_scorer"]["results"]),
        "project_trends": copy.deepcopy(evidence["trend_analysis"]["results"]),
    }


def make_report(
    report_id: str,
    project: str,
    source: str,
    start: str,
    end: str,
    version: int,
    evidence: dict,
    narrative: ReportNarrative,
) -> dict:
    facts = report_facts(evidence)
    risks = {risk["task_id"]: risk for risk in facts["project_risks"]["risks"]}
    mitigations = []
    for proposal in narrative.mitigations:
        risk = risks.get(proposal.task_id, {})
        mitigations.append(
            {
                **proposal.model_dump(),
                # Reference provenance belongs to the application, not the model.
                "evidence_ids": [risk["evidence"]["snapshot_id"]] if risk else [],
                "suggested_owner": risk.get("owner"),
                "severity": risk.get("severity"),
                "status": "Proposed — not assigned or executed",
            }
        )
    limitations = list(
        dict.fromkeys(
            warning for result in evidence.values() for warning in result["warnings"]
        )
    )
    limitations.append(
        "Structured facts and references are checked; narrative interpretations and mitigation feasibility require human review."
    )
    return {
        "report_id": report_id,
        "version": version,
        "project_id": project,
        "source": source,
        "period_start": start,
        "as_of_date": end,
        "synthetic": any(
            task["synthetic"] for task in facts["sprint_progress"]["tasks"]
        ),
        "facts": facts,
        "summary": narrative.summary,
        "mitigations": mitigations,
        "coverage": {name: result["coverage"] for name, result in evidence.items()},
        "limitations": limitations,
    }


def validate_report(report: dict, evidence: dict) -> list[str]:
    """Check fixed facts, scope, references, and proposed mitigation coverage."""
    issues = []
    expected = report_facts(evidence)
    if report["facts"] != expected:
        issues.append("Report facts differ from the collected analysis results.")
    progress = expected["sprint_progress"]
    expected_synthetic = any(task["synthetic"] for task in progress["tasks"])
    if report["synthetic"] != expected_synthetic:
        issues.append("Synthetic-data disclosure does not match the source records.")
    if report["coverage"] != {
        name: result["coverage"] for name, result in evidence.items()
    }:
        issues.append("Coverage metadata differs from the collected evidence.")
    if sum(progress["status_counts"].values()) != progress["task_count"]:
        issues.append("Sprint status counts do not add up to the task total.")
    if len(progress["tasks"]) != progress["task_count"]:
        issues.append("Sprint task list is incomplete.")
    for result in evidence.values():
        if (result["project_id"], result["source"], result["as_of_date"]) != (
            report["project_id"],
            report["source"],
            report["as_of_date"],
        ):
            issues.append("Report project/source/date does not match its evidence.")
    if parse_date(report["period_start"]) >= parse_date(report["as_of_date"]):
        issues.append("Invalid reporting period.")
    if report["period_start"] != expected["project_trends"]["start_date"]:
        issues.append("Trend period does not match the report period.")
    risks = {risk["task_id"]: risk for risk in expected["project_risks"]["risks"]}
    mitigations = report["mitigations"]
    ids = [item["task_id"] for item in mitigations]
    if len(ids) != len(set(ids)) or set(ids) != set(risks):
        issues.append("Provide exactly one proposed mitigation for each flagged risk.")
    known_tasks = {task["task_id"] for task in progress["tasks"]} | set(risks)
    for item in mitigations:
        risk = risks.get(item["task_id"])
        if not risk:
            continue
        allowed = {risk["evidence"]["snapshot_id"]}
        allowed.update(row["snapshot_id"] for row in risk["status_history_evidence"])
        if not item["evidence_ids"] or not set(item["evidence_ids"]) <= allowed:
            issues.append(f"{item['task_id']}: mitigation cites unsupported evidence.")
        if (
            item["suggested_owner"] != risk["owner"]
            or item["severity"] != risk["severity"]
        ):
            issues.append(
                f"{item['task_id']}: owner or severity differs from the risk evidence."
            )
        for reference in [risk["evidence"], *risk["status_history_evidence"]]:
            if reference["observed_on"] > report["as_of_date"]:
                issues.append("Risk evidence is later than the report cutoff.")
    prose = (
        report["summary"]
        + " "
        + " ".join(
            item["proposed_action"] + " " + item["success_check"]
            for item in mitigations
        )
    )
    if set(re.findall(r"\bT-\d+\b", prose)) - known_tasks:
        issues.append("Generated text references an unknown task.")
    return list(dict.fromkeys(issues))


def render_report(report: dict, status: str = "Draft — awaiting review") -> str:
    """Render the same structured facts that were checked, with proposal labels."""
    facts = report["facts"]
    progress = facts["sprint_progress"]
    lines = [
        f"# Weekly Project Status — {report['project_id']}",
        f"Version {report['version']} | {status}",
        f"Period: {report['period_start']} to {report['as_of_date']}",
    ]
    if report["synthetic"]:
        lines.append("**Synthetic-data demonstration.**")
    lines += [
        "\n## Executive summary",
        *[f"- {point}" for point in summary_points(report["summary"])],
        "\n## Sprint progress",
        f"{progress['sprint']['Sprint Name']}: {progress['task_count']} tasks.",
        ", ".join(
            f"{name}: {count}" for name, count in progress["status_counts"].items()
        ),
        "\n## Project-wide blockers and risks",
    ]
    for risk in facts["project_risks"]["risks"]:
        lines.append(
            f"- {risk['task_id']} — {risk['severity']}: {'; '.join(risk['reasons'])}. Evidence: {risk['evidence']['snapshot_id']}."
        )
    for blocker in facts["project_blockers"]["blockers"]:
        lines.append(
            f"- {blocker['task_id']} blocker: {blocker['blocker_note'] or 'Reason not recorded'}. Affected tasks: {', '.join(blocker['affected_task_ids']) or 'None documented'}."
        )
    trends = facts["project_trends"]
    lines += [
        "\n## Changes since the comparison boundary",
        f"Actual observations: {', '.join(trends['start_observation_dates'])} → {', '.join(trends['end_observation_dates'])}.",
        f"Newly observed completed tasks (project-wide): {', '.join(trends['newly_observed_done_ids']) or 'None'}.",
        "\n## Proposed risk mitigation plan",
        "All actions and owners below are suggestions for human review. No action has been assigned or executed.",
    ]
    for plan in report["mitigations"]:
        lines += [
            f"\n### {plan['task_id']} — {plan['severity']}",
            f"Suggested owner: {plan['suggested_owner'] or 'Needs assignment'}.",
            f"Proposed action: {plan['proposed_action']}",
            f"Success check: {plan['success_check']}",
            f"Evidence: {', '.join(plan['evidence_ids'])}.",
        ]
    lines += ["\n## Evidence coverage and limitations"]
    for name, coverage in report["coverage"].items():
        lines.append(
            f"- {name}: observations {', '.join(coverage['observation_dates']) or 'None'}; last sync {coverage['last_successful_sync'] or 'Unknown'}."
        )
    lines += [f"- {note}" for note in report["limitations"]]
    return "\n".join(lines) + "\n"


def report_hash(report: dict) -> str:
    return hashlib.sha256(encoded(report).encode()).hexdigest()


def save_approved_report(database_path: str, report: dict, approved_hash: str) -> dict:
    """Atomically store JSON and Markdown once per approved report version."""
    if report_hash(report) != approved_hash:
        raise ValueError("Report changed after approval; a new review is required.")
    store = Store(database_path)
    try:
        with store.db:
            store.db.execute("""CREATE TABLE IF NOT EXISTS approved_reports (
                report_id TEXT, version INTEGER, project_id TEXT, source TEXT,
                report_hash TEXT, report_json TEXT, markdown TEXT, approved_at TEXT,
                PRIMARY KEY(report_id, version))""")
            existing = store.db.execute(
                "SELECT report_hash FROM approved_reports WHERE report_id=? AND version=?",
                (report["report_id"], report["version"]),
            ).fetchone()
            if existing and existing[0] != approved_hash:
                raise ValueError("An approved report version cannot be overwritten.")
            store.db.execute(
                "INSERT OR IGNORE INTO approved_reports VALUES(?,?,?,?,?,?,?,?)",
                (
                    report["report_id"],
                    report["version"],
                    report["project_id"],
                    report["source"],
                    approved_hash,
                    encoded(report),
                    render_report(report, "Approved"),
                    datetime.now(UTC).isoformat(),
                ),
            )
        return {
            "report_id": report["report_id"],
            "version": report["version"],
            "database": database_path,
            "table": "approved_reports",
        }
    finally:
        store.close()
