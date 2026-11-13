"""Local Streamlit report workspace; all decisions use the existing workflow."""

import sqlite3
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

import streamlit as st

from project_status_agent.config import PROJECT_ROOT, Settings, load_env
from project_status_agent.data.store import workspace_data
from project_status_agent.integrations.http import ServiceError
from project_status_agent.reports.content import report_hash, summary_points
from project_status_agent.reports.exports import download_dropdown
from project_status_agent.reports.library import report_history
from project_status_agent.reports.workflow import run_report
from project_status_agent.ui.charts import (
    period_changes,
    progress_comparison,
    sprint_tasks,
)
from project_status_agent.ui.chat import render_qa

ERRORS = (ValueError, OSError, sqlite3.Error, ServiceError)


def report_status_label(status: str) -> str:
    return {
        "awaiting_review": "Awaiting review",
        "validated_draft": "Awaiting review",
        "saved": "Approved",
        "rejected": "Rejected",
        "needs_attention": "Needs attention",
    }.get(status, "Draft")


def report_label(row: dict) -> str:
    date = datetime.fromisoformat(row["as_of_date"]).strftime("%d %b %Y")
    return f"{row['display_id']} · {date} · v{row['version']} · {report_status_label(row['status'])}"


def main() -> None:
    st.set_page_config(
        page_title="Project Pulse · Weekly Reports", page_icon="◈", layout="wide"
    )
    load_env(str(PROJECT_ROOT / ".env"))
    settings = Settings.from_env(PROJECT_ROOT)
    database_path = settings.database_path
    checkpoint_path = settings.checkpoint_path
    project_id = settings.project_id
    st.markdown(
        """<style>
        .block-container {max-width:1280px;padding-top:2.5rem;padding-bottom:3rem;}
        h1 {letter-spacing:-.045em;font-weight:750;}
        h2,h3 {letter-spacing:-.025em;}
        [data-testid="stMetric"] {background:white;border:1px solid #e1e8ec;border-radius:12px;padding:16px;}
        [data-testid="stMetricLabel"] {color:#526573;}
        [data-testid="stSidebar"] {border-right:1px solid #dce5e9;}
        .eyebrow {font-size:.72rem;font-weight:700;letter-spacing:.16em;color:#0f766e;margin-bottom:8px;}
    </style>""",
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown("### ◈ PROJECT PULSE")
        st.caption("Your weekly delivery review")
        st.divider()
        link_source = st.query_params.get("source", "notion")
        if link_source not in {"notion", "csv"}:
            link_source = "notion"
        st.caption(f"Project: {project_id}")
        source = st.selectbox(
            "Data source",
            ["notion", "csv"],
            index=1 if link_source == "csv" else 0,
            key=f"source_picker_{link_source}",
            format_func=lambda value: (
                "Notion · saved snapshots"
                if value == "notion"
                else "Sample CSV snapshots"
            ),
        )
        if source != link_source:
            st.query_params["source"] = source
            st.query_params.pop("report_id", None)
            st.rerun()
    scope = (project_id, source, database_path, checkpoint_path)
    if st.session_state.get("workspace_scope") != scope:
        st.session_state.workspace_scope = scope
        st.session_state.active_report_id = None
    requested_id = st.query_params.get("report_id", "")
    if requested_id:
        st.session_state.active_report_id = requested_id
    try:
        data = workspace_data(database_path, project_id, source)
        history = report_history(checkpoint_path, project_id, source)
    except ERRORS as error:
        st.error(f"Cannot read this workspace: {error}")
        return

    with st.sidebar:
        page = st.radio("Workspace", ["Weekly reports", "Ask the Agent"])
    if page == "Ask the Agent":
        default_date = (
            datetime.fromisoformat(data["latest_date"]).date()
            if data["latest_date"]
            else datetime.now(ZoneInfo("America/Los_Angeles")).date()
        )
        render_qa(
            database_path, checkpoint_path, project_id, source, default_date, ERRORS
        )
        return

    with st.sidebar:
        st.markdown("#### Create a report")
        default_date = (
            datetime.fromisoformat(data["latest_date"]).date()
            if data["latest_date"]
            else datetime.now(ZoneInfo("America/Los_Angeles")).date()
        )
        with st.form("generate_report"):
            end = st.date_input("Reporting date", value=default_date)
            start = st.date_input(
                "Compare from", value=default_date - timedelta(days=7)
            )
            generate = st.form_submit_button(
                "Generate report",
                type="primary",
                disabled=not bool(data["latest_date"]),
            )
        st.divider()
        st.caption(f"Latest observation: {data['latest_date'] or 'No data'}")

    st.markdown(
        '<div class="eyebrow">DELIVERY INTELLIGENCE / WEEKLY REPORTS</div>',
        unsafe_allow_html=True,
    )
    st.title("Project Pulse Overview")
    st.write("Review progress, understand the risks, and agree on what happens next.")
    if generate:
        if start >= end:
            st.error("Choose a comparison date before the reporting date.")
        else:
            try:
                with st.spinner(
                    "Gathering evidence and preparing mitigation proposals…"
                ):
                    generated = run_report(
                        database_path,
                        checkpoint_path,
                        project_id,
                        source,
                        as_of_date=end.isoformat(),
                        start_date=start.isoformat(),
                    )
                st.session_state.active_report_id = generated["report_id"]
                st.query_params.update(report_id=generated["report_id"], source=source)
                st.rerun()
            except ERRORS as error:
                st.error(f"Report generation could not finish: {error}")

    st.markdown("#### Report library")
    if history:
        labels = {row["report_id"]: report_label(row) for row in history}
        active = st.session_state.active_report_id
        selected = st.selectbox(
            "Select a report",
            list(labels),
            format_func=labels.get,
            index=list(labels).index(active) if active in labels else 0,
            key=f"report_picker_{active}",
            width="stretch",
        )
        # A direct link that does not exist must show an error, not another report.
        if (active in labels or not active) and selected != active:
            st.session_state.active_report_id = selected
            st.query_params.update(report_id=selected, source=source)
            st.rerun()
    else:
        st.caption("Your generated reports will appear here.")

    if data["synthetic"]:
        st.caption(
            "Synthetic-data demonstration · these are fictional project records."
        )

    identifier = st.session_state.active_report_id
    if not identifier and history:
        identifier = history[0]["report_id"]
        st.session_state.active_report_id = identifier
    if not identifier:
        st.info("Generate your first report using the controls on the left.")
        if not data["latest_date"]:
            st.warning(
                "No synced records for this data source. Sync Notion or import the CSV fixtures before generating a report."
            )
        return
    try:
        result = run_report(
            database_path,
            checkpoint_path,
            project_id,
            source,
            report_id=identifier,
            inspect_only=True,
        )
    except ERRORS as error:
        st.error(f"Cannot open this report: {error}")
        return
    report = result["report"]
    status = result["status"]
    if report and data["latest_date"] and report["as_of_date"] < data["latest_date"]:
        st.caption(
            f"Showing a saved report through {report['as_of_date']}. Project data is available through {data['latest_date']}; generate a new report to include it."
        )
    st.divider()
    title, badge = st.columns([4, 1])
    with title:
        display_id = next(
            (row["display_id"] for row in history if row["report_id"] == identifier),
            "Report",
        )
        report_date = report.get("as_of_date")
        date_label = (
            datetime.fromisoformat(report_date).strftime("%d %b %Y")
            if report_date
            else ""
        )
        st.subheader(
            f"{display_id} · Weekly Project Status — {date_label}"
            if date_label
            else "Weekly Project Status"
        )
        st.caption(f"Version {result['version']}")
    with badge:
        st.markdown(f"**{report_status_label(status)}**")
    if result["validation_issues"]:
        st.error("This report needs attention before it can be approved.")
        st.info(
            "Review actions are available after report checks pass. "
            "Generate a new report, or select an Awaiting review report from the list."
        )
        for issue in result["validation_issues"]:
            st.write(f"• {issue}")
    if not report:
        return
    st.caption(
        f"Reporting period {report['period_start']} → {report['as_of_date']} · Evidence and data limitations are included below."
    )
    facts = report["facts"]
    progress = facts["sprint_progress"]
    counts = progress["status_counts"]
    columns = st.columns(4)
    for column, label, value in zip(
        columns,
        ["Sprint tasks", "Done in sprint", "Blocked in sprint", "Project risks"],
        [
            progress["task_count"],
            counts.get("Done", 0),
            counts.get("Blocked", 0),
            facts["project_risks"]["count"],
        ],
        strict=True,
    ):
        column.metric(label, value)

    overview, risks = st.tabs(["Overview", "Risks & mitigation"])
    with overview:
        st.subheader("Executive summary")
        points = "".join(
            f"<li>{escape(point)}</li>" for point in summary_points(report["summary"])
        )
        st.markdown(
            f'<ul style="padding-left:1.5rem;line-height:1.8;list-style-type:disc">{points}</ul>',
            unsafe_allow_html=True,
        )

        st.markdown("#### Progress this period")
        trend = facts["project_trends"]
        before_after, changes = st.columns([3, 2], gap="large")
        if trend["comparable_task_count"]:
            with before_after:
                st.caption("PROJECT STATUS · BEFORE AND AFTER")
                st.altair_chart(progress_comparison(trend), width="stretch")
            with changes:
                st.caption("OBSERVED CHANGES")
                st.altair_chart(period_changes(trend), width="stretch")
        else:
            st.info("There is no comparable baseline for a progress graph.")
        st.caption(
            f"Project-wide changes · actual observations {', '.join(trend['start_observation_dates']) or 'No baseline'} → {', '.join(trend['end_observation_dates']) or 'No observations'}. "
            "Unchanged status does not establish inactivity."
        )
        st.markdown("#### Sprint task progress")
        view = st.radio(
            "Task display", ["Task status map", "Workload by owner"], horizontal=True
        )
        tasks = progress["tasks"]
        if tasks:
            st.altair_chart(sprint_tasks(tasks, view), width="stretch")
            st.caption(
                "Hover for task details. Colors show recorded status; the owner view counts tasks, not hours or capacity."
            )
        else:
            st.info("No tasks assigned to this sprint in the selected observations.")
    with risks:
        st.subheader("Proposed risk mitigation plan")
        st.caption(
            "Actions and owners are proposals for review. Approval saves the report; it does not assign or execute actions."
        )
        risk_by_id = {row["task_id"]: row for row in facts["project_risks"]["risks"]}
        for plan in report["mitigations"]:
            risk = risk_by_id[plan["task_id"]]
            with st.container(border=True):
                st.markdown(f"#### {plan['task_id']} · {risk['title']}")
                st.caption(
                    f"{plan['severity']} risk · suggested owner: {plan['suggested_owner']} · proposed"
                )
                st.write("Recorded risk: " + "; ".join(risk["reasons"]))
                action, success = st.columns([3, 2])
                with action:
                    st.markdown("**Proposed action**")
                    st.write(plan["proposed_action"])
                with success:
                    st.markdown("**Success check**")
                    st.write(plan["success_check"])
        if not report["mitigations"]:
            st.info("No flagged risks requiring mitigation proposals in this report.")
    with st.expander("Source details & review history", expanded=False):
        st.caption(
            "Optional: verify where findings came from and which report version was reviewed."
        )
        dates = sorted(
            {
                date
                for coverage in report["coverage"].values()
                for date in coverage["observation_dates"]
            }
        )
        syncs = sorted(
            {
                coverage["last_successful_sync"]
                for coverage in report["coverage"].values()
                if coverage["last_successful_sync"]
            }
        )
        st.write("Observation dates: " + (", ".join(dates) or "Unknown"))
        st.write("Last successful sync: " + (", ".join(syncs) or "Unknown"))
        st.dataframe(
            [
                {
                    "Risk": plan["task_id"],
                    "Supporting snapshots": ", ".join(plan["evidence_ids"]),
                }
                for plan in report["mitigations"]
            ],
            hide_index=True,
            width="stretch",
        )
        st.markdown("#### Limitations")
        for note in report["limitations"]:
            st.write("• " + note)
        st.markdown("#### Review history")
        if result["review_history"]:
            st.dataframe(
                [
                    {
                        key: row.get(key)
                        for key in ["version", "decision", "feedback", "reviewed_at"]
                    }
                    for row in result["review_history"]
                ],
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("This version has not yet been reviewed.")

    if status == "awaiting_review":
        st.divider()
        st.subheader("Review this report")
        st.caption(
            f"Version {result['version']} · feedback revises the summary and proposed mitigation plan. "
            "It does not change task statuses, owners, deadlines, or Notion records."
        )
        st.caption(
            "To correct project facts: edit the source tasks in Notion, sync the updates, then generate a new report."
        )
        displayed = {
            "report_id": identifier,
            "version": result["version"],
            "hash": report_hash(report),
        }
        previous = st.session_state.get("displayed_review")
        if previous and previous["report_id"] == identifier and previous != displayed:
            st.warning(
                "The report changed since your previous view. Review this version before making a decision."
            )
        st.session_state.displayed_review = displayed
        with st.form(f"review_report_{identifier}_v{result['version']}"):
            feedback = st.text_area(
                "Changes to the report",
                placeholder="Example: make the summary shorter, or suggest a different mitigation approach for T-004.",
                key=f"feedback_{identifier}_v{result['version']}",
            )
            approve_column, revise_column, reject_column = st.columns(3)
            approve = approve_column.form_submit_button(
                "Approve & save", type="primary"
            )
            revise = revise_column.form_submit_button("Request revision")
            reject = reject_column.form_submit_button("Reject report")
        if approve or revise or reject:
            decision = "approve" if approve else "revise" if revise else "reject"
            if revise and not feedback.strip():
                st.warning("Please add feedback before requesting a revision.")
            else:
                try:
                    with st.spinner("Updating your report…"):
                        run_report(
                            database_path,
                            checkpoint_path,
                            project_id,
                            source,
                            report_id=identifier,
                            version=result["version"],
                            decision=decision,
                            feedback=feedback,
                            expected_hash=displayed["hash"],
                        )
                    st.rerun()
                except ERRORS as error:
                    st.error(f"Review could not be completed: {error}")
    elif status == "saved":
        st.success(f"Version {result['version']} is approved and saved locally.")
    elif status == "rejected":
        st.warning(
            "This report was rejected. Any download remains labeled not approved."
        )

    st.divider()
    st.subheader("Download your report")
    if not result["validation_issues"]:
        basename = f"{project_id}-weekly-{report['as_of_date']}-v{report['version']}-{'approved' if status == 'saved' else 'draft'}"
        st.iframe(
            download_dropdown(report, status, basename),
            height=110,
            alt="Report format dropdown; select Word, PDF, or Markdown to download",
        )
