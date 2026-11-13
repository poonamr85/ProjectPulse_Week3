"""Deterministic project analysis. These functions never call a language model.

Each result carries dated evidence and limitations. Current task attributes
(owner, deadline, priority, dependencies) are not historical facts.
"""

from collections import Counter
from datetime import UTC, date, datetime
from typing import Any

from project_status_agent.data.contract import DATA_LIMITATIONS, data_contract
from project_status_agent.data.store import Store

Result = dict[str, Any]
MAX_RESULTS = 100


def parse_date(value: str) -> date:
    """Accept only explicit YYYY-MM-DD reporting dates."""
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Dates must use YYYY-MM-DD.")
    return parsed


def validate_page(limit: int, offset: int = 0) -> None:
    if not 1 <= limit <= MAX_RESULTS or offset < 0:
        raise ValueError("limit must be 1–100 and offset must be nonnegative.")


class ProjectData:
    """A source-scoped view of exact records for one analysis invocation."""

    def __init__(self, store: Store, project_id: str, source: str):
        if source not in {"csv", "notion"}:
            raise ValueError("source must be csv or notion.")
        self.store = store
        self.project_id = project_id
        self.source = source
        self.tasks = {
            row["Task ID"]: row for row in store.records(project_id, source, "tasks")
        }
        self.sprints = {
            row["Sprint ID"]: row
            for row in store.records(project_id, source, "sprints")
        }
        self.history = sorted(
            store.records(project_id, source, "history"),
            key=lambda row: (row["Snapshot Date"], row["Task ID"]),
        )
        if not self.tasks or not self.sprints or not self.history:
            raise ValueError(
                "No complete project dataset. Run sync for this project/source first."
            )

    def latest(self, as_of_date: str) -> dict[str, dict]:
        parse_date(as_of_date)
        latest = {}
        for row in self.history:
            if row["Snapshot Date"] <= as_of_date:
                latest[row["Task ID"]] = row
        return latest

    def sprint(self, as_of_date: str, sprint_id: str | None) -> dict:
        if sprint_id:
            if sprint_id not in self.sprints:
                raise ValueError(f"Unknown sprint: {sprint_id}.")
            return self.sprints[sprint_id]
        matches = [
            row
            for row in self.sprints.values()
            if row["Start Date"] <= as_of_date <= row["End Date"]
        ]
        if len(matches) != 1:
            raise ValueError(
                "Reporting date must identify exactly one sprint; specify sprint_id."
            )
        return matches[0]

    def task_view(self, snapshot: dict) -> dict:
        task_id = snapshot["Task ID"]
        task = self.tasks.get(task_id)
        return {
            "task_id": task_id,
            "title": task["Title"] if task else "Task absent from current inventory",
            "owner": task["Owner"] if task else None,
            "priority": task["Priority"] if task else None,
            "due_date": task["Due Date"] if task else None,
            "depends_on_task_ids": [
                identifier.strip()
                for identifier in task["Depends On Task IDs"].split(",")
                if identifier.strip()
            ]
            if task
            else [],
            "status": snapshot["Status"],
            "sprint_id": snapshot["Assigned Sprint ID"],
            "observed_on": snapshot["Snapshot Date"],
            "blocker_note": snapshot["Blocker Note"],
            "synthetic": snapshot["Data Type"] == "Synthetic",
            "evidence": self.evidence(snapshot),
        }

    def evidence(self, snapshot: dict) -> dict:
        return {
            "snapshot_id": snapshot["Snapshot ID"],
            "task_id": snapshot["Task ID"],
            "observed_on": snapshot["Snapshot Date"],
            "source_url": snapshot.get("_source_url", ""),
        }

    def unchanged_since(self, task_id: str, as_of_date: str) -> tuple[str, list[dict]]:
        """Find the observed suffix with the same status, not continuous inactivity."""
        rows = [
            row
            for row in self.history
            if row["Task ID"] == task_id and row["Snapshot Date"] <= as_of_date
        ]
        if not rows:
            raise ValueError("No observations for this task/date.")
        suffix = [rows[-1]]
        for row in reversed(rows[:-1]):
            if row["Status"] != rows[-1]["Status"]:
                break
            suffix.insert(0, row)
        return suffix[0]["Snapshot Date"], suffix

    def response(
        self, as_of_date: str, results: Any, warnings: list[str] | None = None
    ) -> Result:
        latest = self.latest(as_of_date)
        observed_dates = sorted({row["Snapshot Date"] for row in latest.values()})
        summary = self.store.summary(self.project_id, self.source)
        successful = self.store.db.execute(
            "SELECT finished_at FROM sync_runs "
            "WHERE project=? AND source=? AND status='complete' "
            "ORDER BY id DESC LIMIT 1",
            (self.project_id, self.source),
        ).fetchone()
        notes = list(DATA_LIMITATIONS)
        if not latest:
            notes.append("No observations exist at or before the reporting date.")
        if any(row["Snapshot Date"] < as_of_date for row in latest.values()):
            notes.append(
                "Some task states precede the reporting date; inspect actual observation dates."
            )
        missing = sorted(set(self.tasks) - latest.keys())
        if missing:
            notes.append(
                f"No applicable observations for {len(missing)} current tasks; coverage is incomplete."
            )
        runs = summary["recent_syncs"]
        if runs and runs[0]["status"] != "complete":
            notes.append(
                "Latest sync did not complete; results use previously saved records."
            )
        if successful:
            age = datetime.now(UTC) - datetime.fromisoformat(successful["finished_at"])
            if age.total_seconds() > 86400:
                notes.append("Last successful sync is more than 24 hours old.")
        return {
            "project_id": self.project_id,
            "source": self.source,
            "as_of_date": as_of_date,
            "results": results,
            "data_contract": data_contract(),
            "coverage": {
                "current_inventory_tasks": len(self.tasks),
                "tasks_with_observations": len(latest),
                "missing_task_ids": missing,
                "observation_dates": observed_dates,
                "last_successful_sync": successful["finished_at"]
                if successful
                else None,
            },
            "warnings": list(dict.fromkeys(notes + (warnings or []))),
        }


class AnalysisService:
    """Seven application functions, independent of LangChain and agent prompts."""

    def __init__(
        self, store: Store, project_id: str, source: str = "notion", memory=None
    ):
        self.store = store
        self.project_id = project_id
        self.source = source
        self.memory = memory

    def _data(self) -> ProjectData:
        return ProjectData(self.store, self.project_id, self.source)

    def fetch_sprint_data(
        self, as_of_date: str, sprint_id: str | None = None
    ) -> Result:
        """Read sprint membership and status from latest applicable observations."""
        data = self._data()
        latest = data.latest(as_of_date)
        sprint = data.sprint(as_of_date, sprint_id)
        tasks = [
            data.task_view(row)
            for row in latest.values()
            if row["Assigned Sprint ID"] == sprint["Sprint ID"]
        ]
        counts = dict(Counter(task["status"] for task in tasks))
        sprint_details = {
            key: sprint[key]
            for key in ("Sprint ID", "Sprint Name", "Start Date", "End Date", "Goal")
        }
        sprint_details["phase_as_of"] = (
            "Upcoming"
            if as_of_date < sprint["Start Date"]
            else "Ended"
            if as_of_date > sprint["End Date"]
            else "In progress"
        )
        return data.response(
            as_of_date,
            {
                "sprint": sprint_details,
                "task_count": len(tasks),
                "status_counts": counts,
                "completion_percentage": round(
                    100 * counts.get("Done", 0) / len(tasks), 1
                )
                if tasks
                else None,
                "tasks": tasks[:MAX_RESULTS],
                "truncated": len(tasks) > MAX_RESULTS,
            },
        )

    def detect_blockers(self, as_of_date: str) -> Result:
        """Return explicit blockers and transitive impacts from current dependency links."""
        data = self._data()
        latest = data.latest(as_of_date)
        blockers = []
        for task_id, snapshot in latest.items():
            if snapshot["Status"] != "Blocked":
                continue
            affected, visited, pending = [], {task_id}, [task_id]
            while pending:
                dependency = pending.pop()
                for candidate, task in data.tasks.items():
                    dependencies = [
                        value.strip()
                        for value in task["Depends On Task IDs"].split(",")
                    ]
                    if dependency in dependencies and candidate not in visited:
                        visited.add(candidate)
                        if (
                            candidate in latest
                            and latest[candidate]["Status"] != "Done"
                        ):
                            affected.append(candidate)
                            pending.append(candidate)
            blockers.append(
                {**data.task_view(snapshot), "affected_task_ids": sorted(affected)}
            )
        return data.response(
            as_of_date,
            {
                "count": len(blockers),
                "blockers": blockers[:MAX_RESULTS],
                "truncated": len(blockers) > MAX_RESULTS,
            },
            [
                "Dependency paths indicate potential impact, not proof that every downstream task is blocked."
            ],
        )

    def risk_scorer(self, as_of_date: str) -> Result:
        """Apply explainable severity rules; scores are not failure probabilities."""
        reporting_date = parse_date(as_of_date)
        data = self._data()
        risks = []
        for task_id, snapshot in data.latest(as_of_date).items():
            if snapshot["Status"] == "Done":
                continue
            task = data.tasks.get(task_id)
            if not task:
                continue
            reasons = []
            if snapshot["Status"] == "Blocked":
                reasons.append("Explicitly blocked")
            if parse_date(task["Due Date"]) < reporting_date:
                reasons.append("Incomplete and past current due date")
            since, observations = data.unchanged_since(task_id, as_of_date)
            unchanged_days = (
                parse_date(snapshot["Snapshot Date"]) - parse_date(since)
            ).days
            if (
                snapshot["Status"] == "In progress"
                and len(observations) >= 2
                and unchanged_days >= 7
            ):
                reasons.append(
                    f"In progress across unchanged observations spanning {unchanged_days} days"
                )
            if not reasons:
                continue
            urgent = (
                snapshot["Status"] == "Blocked"
                or parse_date(task["Due Date"]) < reporting_date
            )
            severity = "Medium"
            if urgent:
                severity = "Critical" if task["Priority"] == "Critical" else "High"
            risks.append(
                {
                    **data.task_view(snapshot),
                    "severity": severity,
                    "reasons": reasons,
                    "status_history_evidence": [
                        data.evidence(row) for row in observations
                    ],
                }
            )
        order = {"Critical": 0, "High": 1, "Medium": 2}
        risks.sort(key=lambda row: (order[row["severity"]], row["task_id"]))
        return data.response(
            as_of_date,
            {
                "overall_flag": risks[0]["severity"] if risks else "No identified flag",
                "count": len(risks),
                "risks": risks[:MAX_RESULTS],
                "truncated": len(risks) > MAX_RESULTS,
            },
            [
                "No identified flag is not assurance of low risk, especially with incomplete or stale data."
            ],
        )

    def query_sprint_history(
        self,
        as_of_date: str,
        task_id: str | None = None,
        sprint_id: str | None = None,
        start_date: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Result:
        """Return most recent observations first, with explicit pagination."""
        validate_page(limit, offset)
        parse_date(as_of_date)
        if start_date and parse_date(start_date) > parse_date(as_of_date):
            raise ValueError("start_date must not be after as_of_date.")
        data = self._data()
        if task_id and task_id not in {row["Task ID"] for row in data.history}:
            raise ValueError(f"Unknown task: {task_id}.")
        if sprint_id and sprint_id not in data.sprints:
            raise ValueError(f"Unknown sprint: {sprint_id}.")
        rows = [
            row
            for row in reversed(data.history)
            if row["Snapshot Date"] <= as_of_date
            and (not start_date or row["Snapshot Date"] >= start_date)
            and (not task_id or row["Task ID"] == task_id)
            and (not sprint_id or row["Assigned Sprint ID"] == sprint_id)
        ]
        page = rows[offset : offset + limit]
        return data.response(
            as_of_date,
            {
                "total": len(rows),
                "observations": [data.task_view(row) for row in page],
                "next_offset": offset + limit if offset + limit < len(rows) else None,
            },
        )

    def get_persistent_blockers(
        self,
        as_of_date: str,
        currently_blocked_only: bool = True,
        minimum_sprints: int = 2,
    ) -> Result:
        """Find blocked observations in distinct assigned sprints, not repeated runs."""
        if minimum_sprints < 2:
            raise ValueError("minimum_sprints must be at least 2.")
        data = self._data()
        latest = data.latest(as_of_date)
        blockers = []
        for task_id, snapshot in latest.items():
            if currently_blocked_only and snapshot["Status"] != "Blocked":
                continue
            observations = [
                row
                for row in data.history
                if row["Task ID"] == task_id
                and row["Snapshot Date"] <= as_of_date
                and row["Status"] == "Blocked"
                and row["Assigned Sprint ID"]
            ]
            sprints = sorted({row["Assigned Sprint ID"] for row in observations})
            if len(sprints) >= minimum_sprints:
                blockers.append(
                    {
                        **data.task_view(snapshot),
                        "blocked_sprint_ids": sprints,
                        "blocked_observations": [
                            data.evidence(row) for row in observations
                        ],
                    }
                )
        return data.response(
            as_of_date,
            {
                "count": len(blockers),
                "blockers": blockers[:MAX_RESULTS],
                "truncated": len(blockers) > MAX_RESULTS,
            },
        )

    def trend_analysis(
        self,
        as_of_date: str,
        start_date: str | None = None,
        last_n_snapshots: int | None = None,
    ) -> Result:
        """Compare boundary observations; last N means distinct observation dates."""
        end_date = parse_date(as_of_date)
        if (start_date is None) == (last_n_snapshots is None):
            raise ValueError("Provide exactly one of start_date or last_n_snapshots.")
        data = self._data()
        if last_n_snapshots is not None:
            if not 2 <= last_n_snapshots <= 100:
                raise ValueError("last_n_snapshots must be 2–100.")
            dates = sorted(
                {
                    row["Snapshot Date"]
                    for row in data.history
                    if row["Snapshot Date"] <= as_of_date
                }
            )
            if len(dates) < last_n_snapshots:
                raise ValueError(
                    "Not enough distinct observation dates for the requested comparison."
                )
            start_date = dates[-last_n_snapshots]
        if parse_date(start_date) >= end_date:
            raise ValueError("start_date must be before as_of_date.")
        before, after = data.latest(start_date), data.latest(as_of_date)
        comparable = before.keys() & after.keys()
        transitions, unchanged = [], []
        for task_id in sorted(comparable):
            old, new = before[task_id], after[task_id]
            if old["Status"] != new["Status"]:
                transitions.append(
                    {
                        "task_id": task_id,
                        "from_status": old["Status"],
                        "to_status": new["Status"],
                        "evidence": [data.evidence(old), data.evidence(new)],
                    }
                )
            if new["Status"] == "In progress":
                since, observations = data.unchanged_since(task_id, as_of_date)
                days = (parse_date(new["Snapshot Date"]) - parse_date(since)).days
                if len(observations) >= 2 and days >= 7:
                    unchanged.append(
                        {
                            "task_id": task_id,
                            "first_observed_on": since,
                            "last_observed_on": new["Snapshot Date"],
                            "observed_span_days": days,
                            "evidence": [data.evidence(row) for row in observations],
                        }
                    )
        warnings = []
        if any(row["Snapshot Date"] != start_date for row in before.values()):
            warnings.append(
                "Start boundary uses earlier available observations; comparison is approximate."
            )
        if set(before) != set(after):
            warnings.append("Only tasks observed at both boundaries are comparable.")
        return data.response(
            as_of_date,
            {
                "start_date": start_date,
                "start_observation_dates": sorted(
                    {row["Snapshot Date"] for row in before.values()}
                ),
                "end_observation_dates": sorted(
                    {row["Snapshot Date"] for row in after.values()}
                ),
                "comparable_task_count": len(comparable),
                "newly_observed_task_ids": sorted(after.keys() - before.keys()),
                "status_counts_before": dict(
                    Counter(before[t]["Status"] for t in comparable)
                ),
                "status_counts_after": dict(
                    Counter(after[t]["Status"] for t in comparable)
                ),
                "newly_observed_done_ids": sorted(
                    t
                    for t in comparable
                    if before[t]["Status"] != "Done" and after[t]["Status"] == "Done"
                ),
                "transitions": transitions[:MAX_RESULTS],
                "unchanged_in_progress": unchanged[:MAX_RESULTS],
                "truncated": len(transitions) > MAX_RESULTS
                or len(unchanged) > MAX_RESULTS,
            },
            warnings,
        )

    def search_project_memory(
        self, query: str, as_of_date: str, limit: int = 10
    ) -> Result:
        """Retrieve Mem0 context and verify its evidence against scoped local history."""
        parse_date(as_of_date)
        validate_page(limit)
        if not query.strip():
            raise ValueError("Memory query must not be blank.")
        if self.memory is None:
            raise ValueError(
                "Mem0 is not configured. Set MEM0_API_KEY and MEMORY_USER_ID."
            )
        if self.memory.project != self.project_id:
            raise ValueError("Memory client does not match the selected project.")
        data = self._data()
        memories = self.memory.search(query, self.source, as_of_date, limit)
        snapshots = {row["Snapshot ID"]: row for row in data.history}
        verified = []
        for memory in memories:
            metadata = memory.get("metadata") or {}
            snapshot = snapshots.get(metadata.get("snapshot_id"))
            if not snapshot or snapshot["Snapshot Date"] > as_of_date:
                continue
            if (
                metadata.get("project_id") != self.project_id
                or metadata.get("source") != self.source
            ):
                continue
            if metadata.get("task_id") != snapshot["Task ID"]:
                continue
            verified.append(
                {
                    "memory_id": memory.get("id"),
                    "context": memory.get("memory"),
                    "score": memory.get("score"),
                    "evidence": data.evidence(snapshot),
                    "source_observation": data.task_view(snapshot),
                }
            )
        return data.response(
            as_of_date,
            {"matches": verified},
            [
                "Semantic retrieval is not exhaustive. A memory's source reference is checked, not every claim in its text.",
                "Use structured history for counts and temporal conclusions; unmatched memory references are excluded.",
            ],
        )
