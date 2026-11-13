"""Mem0 Platform adapter. Exact historical analytics remain in SQLite."""

import hashlib
import json

from project_status_agent.data.store import encoded
from project_status_agent.integrations.http import ServiceError, request_json


class ProjectMemory:
    def __init__(self, api_key, user_id, project_id, transport=request_json):
        self.headers = {"Authorization": f"Token {api_key}"}
        # Composite entity namespace prevents collisions across projects for one user.
        self.scope = hashlib.sha256(encoded([user_id, project_id]).encode()).hexdigest()
        self.project = project_id
        self.transport = transport

    def add(self, item):
        metadata = json.loads(item["metadata"])
        if metadata["project_id"] != self.project:
            raise ValueError("Cannot publish another project's memory.")
        response = self.transport(
            "https://api.mem0.ai/v3/memories/add/",
            self.headers,
            {
                "user_id": self.scope,
                "messages": [{"role": "user", "content": item["content"]}],
                "metadata": metadata,
                "infer": False,
                "immutable": True,
            },
            retry=False,
        )
        if response.get("status") == "FAILED":
            raise ServiceError("Mem0 memory creation failed.")
        if response.get("status") == "PENDING":
            if not response.get("event_id"):
                raise ServiceError("Mem0 pending response has no event ID.")
            return "submitted", response
        if response.get("status") == "SUCCEEDED" or response.get("results"):
            return "sent", response
        raise ServiceError(
            "Unrecognized Mem0 add response; delivery requires reconciliation."
        )

    def search(self, query, source, as_of, limit=10):
        from datetime import date

        date.fromisoformat(as_of)
        if not 1 <= limit <= 100:
            raise ValueError("Memory search limit must be between 1 and 100.")
        # Platform accepts custom fields under metadata, not as top-level keys.
        # Metadata range operators are unsupported: filter observation dates locally.
        response = self.transport(
            "https://api.mem0.ai/v3/memories/search/",
            self.headers,
            {
                "query": query,
                "filters": {
                    "AND": [
                        {"user_id": self.scope},
                        {"metadata": {"project_id": self.project, "source": source}},
                    ]
                },
                "top_k": min(limit * 5, 1000),
            },
        )
        if not isinstance(response.get("results"), list):
            raise ServiceError("Invalid Mem0 search response.")
        safe = []
        for result in response["results"]:
            metadata = result.get("metadata") or {}
            # Defense in depth, including for services that ignore a metadata filter.
            observation_date = metadata.get("snapshot_date") or metadata.get(
                "observation_date"
            )
            # Existing fixture memories can omit the reserved observation_date
            # metadata key; their explicit snapshot IDs end in YYYY-MM-DD.
            if not observation_date:
                snapshot_id = metadata.get("snapshot_id", "")
                observation_date = (
                    snapshot_id[-10:] if isinstance(snapshot_id, str) else None
                )
            try:
                observed = date.fromisoformat(observation_date)
            except (ValueError, TypeError):
                continue
            if (
                metadata.get("project_id") == self.project
                and metadata.get("source") == source
                and observed <= date.fromisoformat(as_of)
            ):
                safe.append(result)
        return safe[:limit]

    def poll(self, event_id):
        from uuid import UUID

        # V3 returns UUID event identifiers; validate before interpolating the URL.
        UUID(event_id)
        return self.transport(f"https://api.mem0.ai/v1/event/{event_id}/", self.headers)


def publish_pending(store, memory, project, source, limit=10):
    # Persist 'sending' before the HTTP call. An ambiguous timeout/crash must not
    # silently retry a non-idempotent write and create duplicate remote memories.
    rows = store.db.execute(
        "SELECT * FROM memory_queue WHERE project=? AND source=? AND state='pending' ORDER BY evidence_id LIMIT ?",
        (project, source, limit),
    ).fetchall()
    count = 0
    for row in rows:
        with store.db:
            claimed = store.db.execute(
                "UPDATE memory_queue SET state='sending' WHERE evidence_id=? AND state='pending'",
                (row["evidence_id"],),
            ).rowcount
        if not claimed:
            continue
        try:
            state, receipt = memory.add(row)
        except Exception:
            with store.db:
                store.db.execute(
                    "UPDATE memory_queue SET state='uncertain' WHERE evidence_id=?",
                    (row["evidence_id"],),
                )
            raise
        with store.db:
            store.db.execute(
                "UPDATE memory_queue SET state=?,receipt=? WHERE evidence_id=?",
                (state, encoded(receipt), row["evidence_id"]),
            )
        count += 1
    return count


def reconcile_submitted(store, memory, project, source):
    rows = store.db.execute(
        "SELECT * FROM memory_queue WHERE project=? AND source=? AND state='submitted'",
        (project, source),
    ).fetchall()
    for row in rows:
        receipt = json.loads(row["receipt"])
        result = memory.poll(receipt["event_id"])
        status = result.get("status")
        state = {
            "SUCCEEDED": "sent",
            "FAILED": "failed",
            "PENDING": "submitted",
            "RUNNING": "submitted",
        }.get(status)
        if not state:
            raise ServiceError("Unrecognized Mem0 event status.")
        with store.db:
            store.db.execute(
                "UPDATE memory_queue SET state=? WHERE evidence_id=?",
                (state, row["evidence_id"]),
            )
