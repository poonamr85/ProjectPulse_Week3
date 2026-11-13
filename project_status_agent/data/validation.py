"""Normalize and validate a complete project bundle before any writes."""

import csv
from datetime import date
from pathlib import Path

SCHEMAS = {
    "tasks": (
        "Task ID",
        "Title",
        "Owner",
        "Priority",
        "Due Date",
        "Planned Sprint ID",
        "Current Sprint ID",
        "Status",
        "Status Since",
        "Blocker Note",
        "Depends On Task IDs",
        "Project ID",
        "Data Type",
    ),
    "sprints": (
        "Sprint ID",
        "Sprint Name",
        "Start Date",
        "End Date",
        "Goal",
        "Status",
        "Project ID",
        "Data Type",
    ),
    "history": (
        "Snapshot ID",
        "Task ID",
        "Snapshot Date",
        "Observation Sprint ID",
        "Assigned Sprint ID",
        "Status",
        "Blocker Note",
        "Project ID",
        "Data Type",
    ),
}
IDS = {"tasks": "Task ID", "sprints": "Sprint ID", "history": "Snapshot ID"}
DATES = {
    "tasks": ("Due Date", "Status Since"),
    "sprints": ("Start Date", "End Date"),
    "history": ("Snapshot Date",),
}
STATUSES = {"To do", "In progress", "Blocked", "Done"}


def read_csv_bundle(folder):
    result = {}
    for table, filename in (
        ("tasks", "tasks.csv"),
        ("sprints", "sprints.csv"),
        ("history", "task_history.csv"),
    ):
        with (Path(folder) / filename).open(encoding="utf-8-sig", newline="") as file:
            result[table] = list(csv.DictReader(file))
    return result


def capture_current(raw, observed_on):
    """Append an explicitly dated observation from current tasks, never backfill."""
    date.fromisoformat(observed_on)
    periods = [
        s for s in raw["sprints"] if s["Start Date"] <= observed_on <= s["End Date"]
    ]
    if len(periods) != 1:
        raise ValueError(
            "Observation date must fall within exactly one sprint. Add the sprint first."
        )
    if any(h["Snapshot Date"] >= observed_on for h in raw["history"]):
        raise ValueError(
            "Live capture must be after all supplied historical observations."
        )
    for task in raw["tasks"]:
        raw["history"].append(
            {
                "Snapshot ID": f"{task['Task ID']}-{observed_on}",
                "Task ID": task["Task ID"],
                "Snapshot Date": observed_on,
                "Observation Sprint ID": periods[0]["Sprint ID"],
                "Assigned Sprint ID": task["Current Sprint ID"],
                "Status": task["Status"],
                "Blocker Note": task["Blocker Note"],
                "Project ID": task["Project ID"],
                "Data Type": task["Data Type"],
                "_source_record_id": task.get("_source_record_id", task["Task ID"]),
                "_source_url": task.get("_source_url", ""),
            }
        )
    return raw


def validate_bundle(raw, project_id):
    result = {}
    for table, fields in SCHEMAS.items():
        if not raw.get(table):
            raise ValueError(
                f"{table}: no rows found. Refusing to treat missing data as an empty project."
            )
        rows, seen = [], set()
        for original in raw[table]:
            missing = set(fields) - original.keys()
            if missing:
                raise ValueError(
                    f"{table}: missing columns {', '.join(sorted(missing))}."
                )
            row = {key: str(original[key] or "").strip() for key in fields}
            row.update({k: v for k, v in original.items() if k.startswith("_source_")})
            identifier = row[IDS[table]]
            if not identifier or identifier in seen:
                raise ValueError(
                    f"{table}: empty or duplicate record ID {identifier!r}."
                )
            seen.add(identifier)
            if row["Project ID"] != project_id:
                raise ValueError(
                    f"{table}/{identifier}: unexpected project; select one project per sync."
                )
            if row["Data Type"] not in ("Synthetic", "Real"):
                raise ValueError(
                    f"{table}/{identifier}: Data Type must be Synthetic or Real."
                )
            for column in DATES[table]:
                try:
                    parsed = date.fromisoformat(row[column])
                    if parsed.isoformat() != row[column]:
                        raise ValueError()
                except ValueError:
                    raise ValueError(
                        f"{table}/{identifier}: {column} must be YYYY-MM-DD."
                    ) from None
            if table != "sprints" and row["Status"] not in STATUSES:
                raise ValueError(f"{table}/{identifier}: unexpected status.")
            if table == "tasks" and (
                not row["Owner"]
                or not row["Title"]
                or row["Priority"] not in {"Low", "Medium", "High", "Critical"}
            ):
                raise ValueError(
                    f"tasks/{identifier}: title, owner, or priority invalid."
                )
            if table == "sprints" and row["Start Date"] > row["End Date"]:
                raise ValueError(f"sprints/{identifier}: end precedes start.")
            rows.append(row)
        result[table] = rows
    tasks = {t["Task ID"]: t for t in result["tasks"]}
    sprints = {s["Sprint ID"]: s for s in result["sprints"]}
    for task in tasks.values():
        for key in ("Planned Sprint ID", "Current Sprint ID"):
            if task[key] not in sprints:
                raise ValueError(f"Task {task['Task ID']}: unknown {key}.")
        for dep in filter(
            None, (x.strip() for x in task["Depends On Task IDs"].split(","))
        ):
            if dep not in tasks or dep == task["Task ID"]:
                raise ValueError(f"Task {task['Task ID']}: invalid dependency.")
    observations = set()
    for row in result["history"]:
        if row["Task ID"] not in tasks:
            raise ValueError("History references an unknown task.")
        for key in ("Observation Sprint ID", "Assigned Sprint ID"):
            if row[key] not in sprints and not (
                key == "Assigned Sprint ID" and not row[key]
            ):
                raise ValueError(f"History: unknown {key}.")
        sprint = sprints[row["Observation Sprint ID"]]
        if not sprint["Start Date"] <= row["Snapshot Date"] <= sprint["End Date"]:
            raise ValueError("Snapshot date falls outside its observation sprint.")
        unique = row["Task ID"], row["Snapshot Date"]
        if unique in observations:
            raise ValueError("Duplicate task/date observation.")
        observations.add(unique)
    for task in tasks.values():
        snapshots = [h for h in result["history"] if h["Task ID"] == task["Task ID"]]
        if not snapshots:
            raise ValueError(f"Task {task['Task ID']} has no history.")
        latest = max(snapshots, key=lambda h: h["Snapshot Date"])
        if (
            latest["Status"] != task["Status"]
            or latest["Assigned Sprint ID"] != task["Current Sprint ID"]
        ):
            raise ValueError(
                f"Task {task['Task ID']}: current values disagree with latest snapshot. Add a dated history row before syncing."
            )
        if task["Status Since"] > latest["Snapshot Date"]:
            raise ValueError("Status Since is later than the latest observation.")
    return result
