import copy
import io
import ssl
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError

from project_status_agent.data.store import Store
from project_status_agent.data.validation import (
    capture_current,
    read_csv_bundle,
    validate_bundle,
)
from project_status_agent.integrations.http import ServiceError, request_json
from project_status_agent.integrations.memory import (
    ProjectMemory,
    publish_pending,
    reconcile_submitted,
)
from project_status_agent.integrations.notion import (
    NotionSource,
    notion_id,
    property_value,
)

ROOT = Path(__file__).resolve().parents[1]


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.folder.name) / "db.sqlite3")
        self.bundle = read_csv_bundle(ROOT / "sample_project_data")

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def sync(self, bundle=None):
        return self.store.sync(
            "PRJ-001", "csv", lambda: copy.deepcopy(bundle or self.bundle)
        )

    def test_fixture_and_idempotent_import(self):
        self.assertEqual(self.sync(), {"tasks": 20, "sprints": 3, "history": 120})
        self.sync()
        self.assertEqual(
            self.store.summary("PRJ-001", "csv")["memory_delivery"], {"pending": 120}
        )
        overdue = [
            t["Task ID"]
            for t in self.store.records("PRJ-001", "csv", "tasks")
            if t["Due Date"] < "2026-11-04" and t["Status"] != "Done"
        ]
        self.assertEqual(overdue, ["T-004", "T-006", "T-008", "T-011"])

    def test_invalid_bundle_preserves_previous_records(self):
        self.sync()
        bad = copy.deepcopy(self.bundle)
        bad["tasks"][0]["Current Sprint ID"] = "missing"
        with self.assertRaises(ValueError):
            self.sync(bad)
        summary = self.store.summary("PRJ-001", "csv")
        self.assertEqual(summary["counts"]["tasks"], 20)
        self.assertEqual(summary["recent_syncs"][0]["status"], "failed")

    def test_fetch_failure_preserves_records(self):
        self.sync()

        def fail():
            raise ServiceError("partial pagination failure")

        with self.assertRaises(ServiceError):
            self.store.sync("PRJ-001", "csv", fail)
        self.assertEqual(len(self.store.records("PRJ-001", "csv", "history")), 120)

    def test_history_cannot_be_silently_rewritten(self):
        self.sync()
        bad = copy.deepcopy(self.bundle)
        bad["history"][0]["Blocker Note"] = "altered history"
        with self.assertRaises(ValueError):
            self.sync(bad)
        self.assertEqual(
            self.store.records("PRJ-001", "csv", "history")[0]["Blocker Note"], ""
        )

    def test_duplicate_ids_and_cross_project_rejected(self):
        for change in (
            lambda b: b["tasks"].append(b["tasks"][0]),
            lambda b: b["tasks"][0].update({"Project ID": "OTHER"}),
        ):
            bad = copy.deepcopy(self.bundle)
            change(bad)
            with self.assertRaises(ValueError):
                validate_bundle(bad, "PRJ-001")

    def test_capture_current_observations(self):
        result = validate_bundle(
            capture_current(copy.deepcopy(self.bundle), "2026-11-05"), "PRJ-001"
        )
        self.assertEqual(len(result["history"]), 140)
        self.sync(result)
        self.sync(result)
        self.assertEqual(len(self.store.records("PRJ-001", "csv", "history")), 140)

    def test_mem0_payload_and_no_duplicate_publication(self):
        self.sync()
        calls = []

        def transport(url, headers, body, **kwargs):
            calls.append(body)
            return {"status": "SUCCEEDED", "results": [{"id": "memory-1"}]}

        mem = ProjectMemory("fake", "alice", "PRJ-001", transport)
        self.assertEqual(publish_pending(self.store, mem, "PRJ-001", "csv", 120), 120)
        self.assertEqual(publish_pending(self.store, mem, "PRJ-001", "csv", 120), 0)
        self.assertFalse(calls[0]["infer"])
        self.assertIn("evidence_id", calls[0]["metadata"])

    def test_ambiguous_memory_write_is_not_retried(self):
        self.sync()

        def fail(*args, **kwargs):
            raise ServiceError("timeout")

        with self.assertRaises(ServiceError):
            publish_pending(
                self.store,
                ProjectMemory("fake", "alice", "PRJ-001", fail),
                "PRJ-001",
                "csv",
                1,
            )
        self.assertEqual(
            self.store.summary("PRJ-001", "csv")["memory_delivery"],
            {"pending": 119, "uncertain": 1},
        )

    def test_submitted_memory_is_polled(self):
        self.sync()
        event_id = "12345678-1234-1234-1234-123456789012"

        def transport(url, headers, body=None, **kwargs):
            return (
                {"status": "PENDING", "event_id": event_id}
                if body
                else {"status": "SUCCEEDED"}
            )

        mem = ProjectMemory("fake", "alice", "PRJ-001", transport)
        publish_pending(self.store, mem, "PRJ-001", "csv", 1)
        reconcile_submitted(self.store, mem, "PRJ-001", "csv")
        self.assertEqual(
            self.store.summary("PRJ-001", "csv")["memory_delivery"],
            {"pending": 119, "sent": 1},
        )

    def test_memory_search_filters_scope_and_future_records(self):
        captured = []
        good = {
            "project_id": "PRJ-001",
            "source": "csv",
            "observation_date": "2026-10-30",
        }

        def transport(url, headers, body, **kwargs):
            captured.append(body)
            return {
                "results": [
                    {"memory": "ok", "metadata": good},
                    {"memory": "wrong", "metadata": {**good, "project_id": "OTHER"}},
                    {
                        "memory": "future",
                        "metadata": {**good, "observation_date": "2027-01-01"},
                    },
                ]
            }

        mem = ProjectMemory("fake", "alice", "PRJ-001", transport)
        self.assertEqual(
            [r["memory"] for r in mem.search("blockers", "csv", "2026-11-04")], ["ok"]
        )
        self.assertNotEqual(mem.scope, ProjectMemory("fake", "bob", "PRJ-001").scope)
        self.assertIn("AND", captured[0]["filters"])
        conditions = captured[0]["filters"]["AND"]
        self.assertEqual(
            conditions[1], {"metadata": {"project_id": "PRJ-001", "source": "csv"}}
        )
        self.assertFalse(
            any("observation_date" in condition for condition in conditions)
        )

    def test_legacy_snapshot_date_fallback_and_missing_dates(self):
        base = {"project_id": "PRJ-001", "source": "notion"}

        def transport(*args, **kwargs):
            return {
                "results": [
                    {
                        "memory": "past",
                        "metadata": {**base, "snapshot_id": "T-004-2026-10-30"},
                    },
                    {
                        "memory": "future",
                        "metadata": {**base, "snapshot_id": "T-004-2026-11-05"},
                    },
                    {"memory": "undated", "metadata": base},
                ]
            }

        mem = ProjectMemory("fake", "alice", "PRJ-001", transport)
        self.assertEqual(
            [r["memory"] for r in mem.search("blockers", "notion", "2026-11-04")],
            ["past"],
        )


class ConnectorTests(unittest.TestCase):
    def test_blank_notion_page_skipped_but_partial_record_preserved(self):
        uid = "12345678123412341234123456789012"

        def transport(url, headers, body=None):
            return {
                "results": [
                    {
                        "id": "blank",
                        "properties": {"Task ID": {"type": "title", "title": []}},
                    },
                    {
                        "id": "partial",
                        "properties": {
                            "Task ID": {"type": "title", "title": []},
                            "Owner": {
                                "type": "rich_text",
                                "rich_text": [{"plain_text": "Maya"}],
                            },
                        },
                    },
                ],
                "has_more": False,
            }

        rows = NotionSource("fake", transport=transport).read(data_source_id=uid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["_source_record_id"], "partial")

    def test_certificate_failure_has_actionable_message_without_retry(self):
        def fail(req, timeout):
            raise URLError(ssl.SSLCertVerificationError("missing trust roots"))

        with self.assertRaisesRegex(ServiceError, "Install Certificates.command"):
            request_json(
                "https://example.com",
                {},
                opener=fail,
                sleep=lambda n: self.fail("Certificate error should not retry"),
            )

    def test_notion_pagination(self):
        calls = []
        uid = "12345678123412341234123456789012"

        def transport(url, headers, body=None):
            calls.append(body)
            if body is None:
                return {"data_sources": [{"id": uid}]}
            if "start_cursor" not in body:
                return {
                    "results": [
                        {
                            "id": "a",
                            "properties": {
                                "Task ID": {
                                    "type": "title",
                                    "title": [{"plain_text": "T-001"}],
                                }
                            },
                        }
                    ],
                    "has_more": True,
                    "next_cursor": "next",
                }
            return {"results": [], "has_more": False}

        rows = NotionSource("fake", transport=transport).read(uid)
        self.assertEqual(rows[0]["Task ID"], "T-001")
        self.assertEqual(calls[-1]["start_cursor"], "next")
        self.assertEqual(notion_id(f"https://notion.so/{uid}?v={'a' * 32}"), uid)

    def test_unsupported_property_is_explicit(self):
        with self.assertRaises(ValueError):
            property_value({"type": "people", "people": []})

    def test_transient_read_retries_but_access_error_does_not(self):
        calls = []

        def transient(req, timeout):
            calls.append(req)
            if len(calls) < 3:
                raise URLError("offline")
            return io.BytesIO(b'{"ok":true}')

        self.assertEqual(
            request_json(
                "https://example.com", {}, opener=transient, sleep=lambda n: None
            ),
            {"ok": True},
        )
        self.assertEqual(len(calls), 3)

        def denied(req, timeout):
            raise HTTPError(req.full_url, 401, "secret response", {}, None)

        with self.assertRaisesRegex(ServiceError, "check credentials"):
            request_json("https://example.com", {}, opener=denied)


if __name__ == "__main__":
    unittest.main()
