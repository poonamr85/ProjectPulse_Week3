"""Transactional project snapshots, sync audit log, and memory delivery queue."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from project_status_agent.data.validation import IDS, validate_bundle


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS records (
                project TEXT, source TEXT, kind TEXT, record_id TEXT,
                payload TEXT NOT NULL, imported_at TEXT NOT NULL,
                PRIMARY KEY (project, source, kind, record_id));
            CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY, project TEXT, source TEXT,
                started_at TEXT, finished_at TEXT, status TEXT, detail TEXT);
            CREATE TABLE IF NOT EXISTS memory_queue (
                evidence_id TEXT PRIMARY KEY, project TEXT, source TEXT,
                content TEXT NOT NULL, metadata TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', receipt TEXT);
        """)

    def close(self):
        self.db.close()

    def sync(self, project, source, fetch):
        now = datetime.now(UTC).isoformat()
        with self.db:
            run = self.db.execute(
                "INSERT INTO sync_runs(project,source,started_at,status) VALUES(?,?,?,'running')",
                (project, source, now),
            ).lastrowid
        try:
            bundle = validate_bundle(fetch(), project)
            with self.db:
                for kind, rows in bundle.items():
                    # Current task/sprint collections replace atomically; exact history is immutable.
                    if kind != "history":
                        self.db.execute(
                            "DELETE FROM records WHERE project=? AND source=? AND kind=?",
                            (project, source, kind),
                        )
                    for row in rows:
                        identifier = row[IDS[kind]]
                        payload = encoded(row)
                        old = self.db.execute(
                            "SELECT payload FROM records WHERE project=? AND source=? AND kind=? AND record_id=?",
                            (project, source, kind, identifier),
                        ).fetchone()
                        if kind == "history" and old:
                            # Provenance URLs can change, but historical facts cannot silently change.
                            facts = lambda r: {
                                k: v
                                for k, v in r.items()
                                if not k.startswith("_source_")
                            }
                            if facts(json.loads(old[0])) != facts(row):
                                raise ValueError(
                                    f"Historical snapshot {identifier} changed. Use an explicit correction process."
                                )
                        self.db.execute(
                            "INSERT OR REPLACE INTO records VALUES(?,?,?,?,?,?)",
                            (project, source, kind, identifier, payload, now),
                        )
                        if kind == "history":
                            self._queue(project, source, row, bundle)
                self.db.execute(
                    "UPDATE sync_runs SET status='complete',finished_at=?,detail=? WHERE id=?",
                    (
                        datetime.now(UTC).isoformat(),
                        encoded({k: len(v) for k, v in bundle.items()}),
                        run,
                    ),
                )
            return {k: len(v) for k, v in bundle.items()}
        except Exception:
            with self.db:
                self.db.execute(
                    "UPDATE sync_runs SET status='failed',finished_at=?,detail='Fetch or validation failed; prior records preserved' WHERE id=?",
                    (datetime.now(UTC).isoformat(), run),
                )
            raise

    def _queue(self, project, source, row, bundle):
        # Store dated source observations, not an LLM's interpretation of them.
        task = next(t for t in bundle["tasks"] if t["Task ID"] == row["Task ID"])
        key = hashlib.sha256(
            encoded([project, source, row["Snapshot ID"]]).encode()
        ).hexdigest()
        text = f"[{row['Data Type']}] Project {project}, task {row['Task ID']} ({task['Title']}), observed {row['Snapshot Date']}: status {row['Status']}; assigned sprint {row['Assigned Sprint ID'] or 'backlog'}."
        if row["Blocker Note"]:
            text += f" Blocker: {row['Blocker Note']}."
        metadata = {
            "project_id": project,
            "source": source,
            "task_id": row["Task ID"],
            "observation_date": row["Snapshot Date"],
            "sprint_id": row["Assigned Sprint ID"],
            "evidence_id": key,
            "snapshot_id": row["Snapshot ID"],
            "synthetic": row["Data Type"] == "Synthetic",
            "kind": "source_observation",
        }
        metadata["snapshot_date"] = row["Snapshot Date"]
        self.db.execute(
            "INSERT OR IGNORE INTO memory_queue(evidence_id,project,source,content,metadata) VALUES(?,?,?,?,?)",
            (key, project, source, text, encoded(metadata)),
        )

    def records(self, project, source, kind):
        return [
            json.loads(r[0])
            for r in self.db.execute(
                "SELECT payload FROM records WHERE project=? AND source=? AND kind=? ORDER BY record_id",
                (project, source, kind),
            )
        ]

    def summary(self, project, source):
        counts = {kind: len(self.records(project, source, kind)) for kind in IDS}
        runs = [
            dict(r)
            for r in self.db.execute(
                "SELECT id,started_at,finished_at,status,detail FROM sync_runs WHERE project=? AND source=? ORDER BY id DESC LIMIT 5",
                (project, source),
            )
        ]
        queue = {
            r[0]: r[1]
            for r in self.db.execute(
                "SELECT state,count(*) FROM memory_queue WHERE project=? AND source=? GROUP BY state",
                (project, source),
            )
        }
        return {
            "project": project,
            "source": source,
            "counts": counts,
            "memory_delivery": queue,
            "recent_syncs": runs,
        }


def workspace_data(database_path: str, project_id: str, source: str) -> dict:
    store = Store(database_path)
    try:
        dates = [
            row["Snapshot Date"] for row in store.records(project_id, source, "history")
        ]
        tasks = store.records(project_id, source, "tasks")
        return {
            "synthetic": bool(tasks)
            and all(row["Data Type"] == "Synthetic" for row in tasks),
            "latest_date": max(dates) if dates else None,
            "summary": store.summary(project_id, source),
        }
    finally:
        store.close()


def latest_observation_date(
    database_path: str, project_id: str, source: str, cutoff: str
) -> str | None:
    """Latest saved observation at or before a conversation's reporting date."""
    store = Store(database_path)
    try:
        return max(
            (
                row["Snapshot Date"]
                for row in store.records(project_id, source, "history")
                if row["Snapshot Date"] <= cutoff
            ),
            default=None,
        )
    finally:
        store.close()
