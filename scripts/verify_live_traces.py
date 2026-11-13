"""Explicit live acceptance check. Uses Nebius credits and sends synthetic traces.

Run from the project directory: python -m scripts.verify_live_traces
Creates a new report draft but never approves, publishes, or executes mitigations.
"""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from project_status_agent.config import Settings, load_env, required


def main():
    load_env()
    if os.environ.get("LANGSMITH_TRACING", "").lower() != "true":
        raise ValueError("Enable LANGSMITH_TRACING before the live check.")
    required("LANGSMITH_API_KEY")
    # Import after configuration so SDKs see the chosen tracing destination.
    from langsmith import Client, trace

    from project_status_agent.agent.runner import run_agent
    from project_status_agent.reports.workflow import run_report

    settings = Settings.from_env()
    database = settings.database_path
    checkpoints = settings.checkpoint_path
    project = settings.project_id
    client = Client(timeout_ms=15000)
    agent = client.agents.retrieve(required("LANGSMITH_AGENT_ID"))
    environment = required("LANGSMITH_AGENT_ENVIRONMENT").upper()
    destination = next(
        item.tracer_session_name
        for item in agent.environments
        if item.environment == environment
    )
    folder = Path("data/verification") / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    folder.mkdir(parents=True, exist_ok=True)
    record = {"destination": destination, "reporting_date": "2026-11-04", "checks": {}}

    print("Running live agent acceptance question…", flush=True)
    with trace(
        "Acceptance - Agent investigation",
        client=client,
        tags=["acceptance", "agent"],
        inputs={
            "question": "Which tasks have been blocked across multiple sprints, and why? Cite snapshot evidence.",
            "as_of_date": "2026-11-04",
        },
    ) as root:
        result = run_agent(
            database,
            checkpoints,
            project,
            "notion",
            question="Which tasks have been blocked across multiple sprints, and why? Cite snapshot evidence.",
            as_of_date="2026-11-04",
            thread_id=f"acceptance-{uuid4()}",
        )
        root.end(
            outputs={
                "status": result["status"],
                "thread_id": result["thread_id"],
                "tool_calls": result["tool_calls"],
            }
        )
        record["checks"]["agent"] = {
            "trace_id": str(root.id),
            "thread_id": result["thread_id"],
            "status": result["status"],
            "model_calls": result["model_calls"],
            "tool_calls": result["tool_calls"],
            "tool_errors": result["tool_errors"],
            "tool_trace": result["tool_trace"],
            "expected_task_ids_present": all(
                task in result["answer"] for task in ["T-004", "T-008"]
            ),
        }
        (folder / "agent-result.json").write_text(json.dumps(result, indent=2))
    print("Agent status:", result["status"], flush=True)
    (folder / "live-trace-check.json").write_text(json.dumps(record, indent=2))
    if result["status"] != "completed":
        raise RuntimeError(
            f"Agent did not complete; inspect {folder / 'agent-result.json'}."
        )

    print("Generating live report draft; it will remain awaiting review…", flush=True)
    with trace(
        "Acceptance - Weekly report",
        client=client,
        tags=["acceptance", "report"],
        inputs={"as_of_date": "2026-11-04"},
    ) as root:
        report = run_report(
            database, checkpoints, project, "notion", as_of_date="2026-11-04"
        )
        root.end(
            outputs={
                "report_id": report["report_id"],
                "version": report["version"],
                "status": report["status"],
                "validation_issues": report["validation_issues"],
            }
        )
        record["checks"]["report"] = {
            "trace_id": str(root.id),
            "report_id": report["report_id"],
            "version": report["version"],
            "status": report["status"],
            "validation_issues": report["validation_issues"],
            "risk_count": len(report["report"].get("mitigations", [])),
        }
        (folder / "report-result.json").write_text(json.dumps(report, indent=2))
    print("Report status:", report["status"], flush=True)
    client.flush(timeout=30)

    for name, check in record["checks"].items():
        runs = []
        for _ in range(3):
            runs = list(
                client.list_runs(
                    project_name=destination, trace_id=check["trace_id"], limit=100
                )
            )
            if any(run.run_type == "llm" for run in runs):
                break
            time.sleep(2)
        root = next((run for run in runs if str(run.id) == check["trace_id"]), None)
        check["trace_verified"] = root is not None
        check["model_spans"] = sum(run.run_type == "llm" for run in runs)
        check["tool_spans"] = [run.name for run in runs if run.run_type == "tool"]
        check["node_spans"] = [run.name for run in runs if run.run_type == "chain"]
        if root:
            check["url"] = client.get_run_url(run=root)
        print(
            name,
            "trace verified:",
            check["trace_verified"],
            "model spans:",
            check["model_spans"],
            "tool spans:",
            check["tool_spans"],
            flush=True,
        )
    (folder / "live-trace-check.json").write_text(json.dumps(record, indent=2))
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
