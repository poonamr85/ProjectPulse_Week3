"""Saved report discovery and permanent display numbers."""

import sqlite3
from contextlib import closing
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


def assign_report_numbers(checkpoint_path: str, reports: list[dict]) -> None:
    """Persist friendly labels separately from report content and approval hashes."""
    with closing(sqlite3.connect(checkpoint_path)) as connection, connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS report_display_numbers (
            number INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id TEXT UNIQUE NOT NULL, project_id TEXT NOT NULL, source TEXT NOT NULL
        )""")
        for row in sorted(reports, key=lambda report: report["created_at"]):
            exists = connection.execute(
                "SELECT number FROM report_display_numbers WHERE report_id=?",
                (row["report_id"],),
            ).fetchone()
            if not exists:
                connection.execute(
                    "INSERT OR IGNORE INTO report_display_numbers(report_id,project_id,source) VALUES(?,?,?)",
                    (row["report_id"], row["project_id"], row["source"]),
                )
            number = connection.execute(
                "SELECT number FROM report_display_numbers WHERE report_id=?",
                (row["report_id"],),
            ).fetchone()[0]
            row["display_id"] = f"RPT-{number:03d}"


def report_history(checkpoint_path: str, project_id: str, source: str) -> list[dict]:
    """List latest report versions in this scope, without model requests."""
    if not Path(checkpoint_path).exists():
        return []
    reports = {}
    with SqliteSaver.from_conn_string(checkpoint_path) as saver:
        for saved in saver.list(None):
            if not saved.config["configurable"]["thread_id"].startswith("report:"):
                continue
            state = saved.checkpoint.get("channel_values", {})
            identifier = state.get("report_id")
            if not identifier:
                continue
            if identifier in reports:
                reports[identifier]["created_at"] = min(
                    reports[identifier]["created_at"], saved.checkpoint["ts"]
                )
                continue
            if (state.get("project_id"), state.get("source")) != (project_id, source):
                continue
            pending = any(
                channel == "__interrupt__"
                for _, channel, _ in saved.pending_writes or []
            )
            reports[identifier] = {
                "report_id": identifier,
                "project_id": project_id,
                "source": source,
                "created_at": saved.checkpoint["ts"],
                "version": state.get("version", 1),
                "as_of_date": state.get("as_of_date", ""),
                "status": "awaiting_review"
                if pending
                else state.get("status", "unknown"),
            }
    rows = list(reports.values())
    if rows:
        assign_report_numbers(checkpoint_path, rows)
    return rows
