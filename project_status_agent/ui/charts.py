"""Interactive report charts derived only from the frozen report facts."""

import json
from collections import Counter
from functools import lru_cache

import altair as alt
import pandas as pd

STATUSES = ["To do", "In progress", "Blocked", "Done"]
STATUS_COLORS = ["#94a3b8", "#d99522", "#d14b4b", "#0f766e"]


def report_chart_assets(report: dict) -> tuple[dict, ...]:
    """Render UI-equivalent charts once per frozen report, without a browser."""
    return _chart_assets(json.dumps(report["facts"], sort_keys=True))


@lru_cache(maxsize=16)
def _chart_assets(facts_json: str) -> tuple[dict, ...]:
    import vl_convert as vlc

    facts = json.loads(facts_json)
    trend = facts["project_trends"]
    charts = []
    if trend["comparable_task_count"]:
        charts += [
            ("Project status: before and after", progress_comparison(trend)),
            ("Observed changes this period", period_changes(trend)),
        ]
    tasks = facts["sprint_progress"]["tasks"]
    if tasks:
        charts.append(
            (
                "Sprint tasks by owner and status",
                sprint_tasks(tasks, "Workload by owner"),
            )
        )
    assets = []
    for title, chart in charts:
        specification = chart.properties(width=540).to_dict()
        assets.append(
            {
                "title": title,
                "png": vlc.vegalite_to_png(specification, scale=2),
                "svg": vlc.vegalite_to_svg(specification),
            }
        )
    return tuple(assets)


def progress_comparison(trend: dict) -> alt.LayerChart:
    """Compare the same observed task population at the two boundaries."""
    rows = [
        {"Status": status, "Period": period, "Tasks": counts.get(status, 0)}
        for period, counts in [
            ("Earlier observation", trend["status_counts_before"]),
            ("Reporting observation", trend["status_counts_after"]),
        ]
        for status in STATUSES
    ]
    base = alt.Chart(pd.DataFrame(rows)).encode(
        x=alt.X("Status:N", sort=STATUSES, axis=alt.Axis(labelAngle=0)),
        xOffset=alt.XOffset(
            "Period:N", sort=["Earlier observation", "Reporting observation"]
        ),
        y=alt.Y("Tasks:Q", axis=alt.Axis(tickMinStep=1)),
        color=alt.Color(
            "Period:N",
            scale=alt.Scale(
                domain=["Earlier observation", "Reporting observation"],
                range=["#a8bac3", "#0f766e"],
            ),
            legend=alt.Legend(orient="bottom", title=None),
        ),
        tooltip=["Status:N", "Period:N", "Tasks:Q"],
    )
    return (
        base.mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        + base.mark_text(dy=-8, color="#243c49").encode(text="Tasks:Q")
    ).properties(height=260)


def period_changes(trend: dict) -> alt.LayerChart:
    rows = [
        {"Change": "Newly completed", "Tasks": len(trend["newly_observed_done_ids"])},
        {
            "Change": "Newly blocked",
            "Tasks": sum(
                row["to_status"] == "Blocked" and row["from_status"] != "Blocked"
                for row in trend["transitions"]
            ),
        },
        {
            "Change": "Unchanged in progress",
            "Tasks": len(trend["unchanged_in_progress"]),
        },
    ]
    base = alt.Chart(pd.DataFrame(rows)).encode(
        y=alt.Y("Change:N", sort=[row["Change"] for row in rows], title=None),
        x=alt.X("Tasks:Q", axis=alt.Axis(tickMinStep=1)),
        color=alt.Color(
            "Change:N",
            scale=alt.Scale(
                domain=[row["Change"] for row in rows],
                range=["#0f766e", "#d14b4b", "#d99522"],
            ),
            legend=None,
        ),
        tooltip=["Change:N", "Tasks:Q"],
    )
    return (
        base.mark_bar(cornerRadiusEnd=4)
        + base.mark_text(align="left", dx=7, color="#243c49").encode(text="Tasks:Q")
    ).properties(height=260)


def sprint_tasks(tasks: list[dict], view: str) -> alt.Chart:
    """Display task status or owner workload without inventing task durations."""
    if view == "Workload by owner":
        counts = Counter((task["owner"], task["status"]) for task in tasks)
        rows = [
            {"Owner": owner, "Status": status, "Tasks": count}
            for (owner, status), count in sorted(counts.items())
        ]
        return (
            alt.Chart(pd.DataFrame(rows))
            .mark_bar()
            .encode(
                y=alt.Y("Owner:N", title=None),
                x=alt.X("Tasks:Q", stack="zero", axis=alt.Axis(tickMinStep=1)),
                color=alt.Color(
                    "Status:N",
                    scale=alt.Scale(domain=STATUSES, range=STATUS_COLORS),
                    legend=alt.Legend(orient="bottom"),
                ),
                tooltip=["Owner:N", "Status:N", "Tasks:Q"],
            )
            .properties(height=max(200, len({task["owner"] for task in tasks}) * 50))
        )
    rows = [
        {
            "Task": f"{task['task_id']} · {task['title']}",
            "ID": task["task_id"],
            "Owner": task["owner"],
            "Status": task["status"],
            "Due": task["due_date"],
        }
        for task in tasks
    ]
    selection = alt.selection_point(fields=["Status"], bind="legend")
    return (
        alt.Chart(pd.DataFrame(rows))
        .mark_circle(size=220)
        .encode(
            y=alt.Y(
                "Task:N",
                sort=[row["Task"] for row in rows],
                title=None,
                axis=alt.Axis(labelLimit=340),
            ),
            x=alt.X(
                "Status:N",
                sort=STATUSES,
                title=None,
                axis=alt.Axis(labelAngle=0, orient="top"),
            ),
            color=alt.Color(
                "Status:N",
                scale=alt.Scale(domain=STATUSES, range=STATUS_COLORS),
                legend=alt.Legend(orient="bottom"),
            ),
            opacity=alt.condition(selection, alt.value(1), alt.value(0.2)),
            tooltip=["ID:N", "Task:N", "Owner:N", "Status:N", "Due:N"],
        )
        .add_params(selection)
        .properties(height=max(220, len(rows) * 34))
    )
