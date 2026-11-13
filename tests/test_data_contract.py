"""Regression coverage for the six observed grounding problems.

These tests verify real schema, tool outputs, scope, and model-facing warnings.
They do not pretend a scripted model answer proves live narrative correctness.
"""

import copy
import tempfile
import unittest
from pathlib import Path

from project_status_agent.agent.tools import create_tools
from project_status_agent.data.analysis import AnalysisService
from project_status_agent.data.contract import (
    CURRENT_ONLY_FIELDS,
    DATA_LIMITATIONS,
    HISTORICAL_FIELDS,
    data_contract,
)
from project_status_agent.data.store import Store
from project_status_agent.data.validation import SCHEMAS, read_csv_bundle

ROOT = Path(__file__).resolve().parents[1]
CUTOFF = "2026-11-10"


class DataContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "records.sqlite3")
        self.bundle = read_csv_bundle(ROOT / "sample_project_data_nov10")
        self.store = Store(self.path)
        self.store.sync("PRJ-001", "csv", lambda: copy.deepcopy(self.bundle))
        self.analysis = AnalysisService(self.store, "PRJ-001", "csv")
        self.tools = {
            tool.name: tool for tool in create_tools(self.path, "PRJ-001", "csv")
        }

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def history(self, **arguments):
        return self.tools["query_sprint_history"].invoke(
            {"as_of_date": CUTOFF, "task_id": "T-008", "limit": 100, **arguments}
        )

    def test_contract_matches_schema_and_reaches_tool_responses(self):
        self.assertTrue(set(HISTORICAL_FIELDS) <= set(SCHEMAS["history"]))
        self.assertTrue(set(CURRENT_ONLY_FIELDS) <= set(SCHEMAS["tasks"]))
        self.assertFalse(set(CURRENT_ONLY_FIELDS) & set(SCHEMAS["history"]))
        changed = data_contract()
        changed["historical_fields"].clear()
        self.assertEqual(data_contract()["historical_fields"], list(HISTORICAL_FIELDS))
        result = self.history()
        self.assertEqual(result["data_contract"], data_contract())
        self.assertTrue(set(DATA_LIMITATIONS) <= set(result["warnings"]))

    def test_historical_sprint_membership_is_returned_with_snapshot_dates(self):
        result = self.history()
        by_date = {row["observed_on"]: row for row in result["results"]["observations"]}
        self.assertEqual(by_date["2026-10-23"]["sprint_id"], "SPR-02")
        self.assertEqual(by_date[CUTOFF]["sprint_id"], "SPR-03")
        self.assertEqual(
            by_date["2026-10-23"]["evidence"]["snapshot_id"], "T-008-2026-10-23"
        )
        self.assertEqual(by_date[CUTOFF]["evidence"]["snapshot_id"], "T-008-2026-11-10")
        past = self.history(as_of_date="2026-10-30")
        self.assertTrue(
            all(
                row["observed_on"] <= "2026-10-30"
                for row in past["results"]["observations"]
            )
        )
        self.assertNotIn(
            "SPR-03", {row["sprint_id"] for row in past["results"]["observations"]}
        )

    def test_historical_blocker_note_change_is_returned_not_declared_unavailable(self):
        result = self.history(start_date="2026-11-04")
        by_date = {row["observed_on"]: row for row in result["results"]["observations"]}
        self.assertEqual(
            by_date["2026-11-04"]["blocker_note"],
            "Depends on T-004 payment integration",
        )
        self.assertEqual(
            by_date[CUTOFF]["blocker_note"],
            "Receipt webhook configuration pending from payment provider; payment integration is complete",
        )
        self.assertIn("Blocker Note", result["data_contract"]["historical_fields"])
        first_page = self.history(start_date="2026-11-04", limit=1)
        next_page = self.history(
            start_date="2026-11-04",
            limit=100,
            offset=first_page["results"]["next_offset"],
        )
        self.assertIn(
            "2026-11-04",
            {row["observed_on"] for row in next_page["results"]["observations"]},
        )

    def test_ownership_history_is_unavailable_even_when_old_rows_show_owner(self):
        updated = copy.deepcopy(self.bundle)
        next(task for task in updated["tasks"] if task["Task ID"] == "T-008")[
            "Owner"
        ] = "Changed current owner"
        self.store.sync("PRJ-001", "csv", lambda: updated)
        result = self.history()
        self.assertTrue(
            all(
                row["owner"] == "Changed current owner"
                for row in result["results"]["observations"]
            )
        )
        self.assertNotIn("Owner", SCHEMAS["history"])
        self.assertIn("Owner", result["data_contract"]["current_only_fields"])
        self.assertNotIn("Owner", result["data_contract"]["historical_fields"])
        provenance = result["data_contract"]["current_attribute_source"]
        self.assertIn("joined from the current Tasks record", provenance)
        self.assertIn("not historical ownership evidence", provenance)
        self.assertIn(provenance, result["warnings"])

    def test_task_due_on_cutoff_is_not_overdue_but_is_after_cutoff(self):
        overdue_reason = "Incomplete and past current due date"
        for cutoff, expected_overdue in [
            ("2026-11-09", False),
            (CUTOFF, False),
            ("2026-11-11", True),
        ]:
            with self.subTest(cutoff=cutoff):
                response = self.tools["risk_scorer"].invoke({"as_of_date": cutoff})
                task = next(
                    row
                    for row in response["results"]["risks"]
                    if row["task_id"] == "T-014"
                )
                self.assertEqual(task["due_date"], CUTOFF)
                self.assertEqual(overdue_reason in task["reasons"], expected_overdue)
                self.assertEqual(response["as_of_date"], cutoff)
        # The later comparison evaluates the last observed state, not a new snapshot.
        self.assertEqual(task["observed_on"], CUTOFF)

    def test_dependency_ids_are_explicit_and_critical_path_is_not_available(self):
        result = self.tools["detect_blockers"].invoke({"as_of_date": CUTOFF})
        blockers = {row["task_id"]: row for row in result["results"]["blockers"]}
        self.assertEqual(blockers["T-008"]["depends_on_task_ids"], ["T-004"])
        self.assertEqual(blockers["T-013"]["depends_on_task_ids"], ["T-004"])
        self.assertNotIn("T-013", blockers["T-008"]["depends_on_task_ids"])
        self.assertNotIn("T-008", blockers["T-013"]["depends_on_task_ids"])
        self.assertIn(
            "A calculated critical path", result["data_contract"]["not_recorded"]
        )
        self.assertTrue(
            any(
                "Similar blocker wording does not confirm" in warning
                for warning in result["warnings"]
            )
        )

    def test_similar_blocker_wording_does_not_create_dependency_edges(self):
        bundle = copy.deepcopy(self.bundle)
        for task in bundle["tasks"]:
            if task["Task ID"] in {"T-008", "T-013"}:
                task["Blocker Note"] = "Shared webhook issue; possibly related"
        for row in bundle["history"]:
            if row["Task ID"] in {"T-008", "T-013"} and row["Snapshot Date"] == CUTOFF:
                row["Blocker Note"] = "Shared webhook issue; possibly related"
        store = Store(str(Path(self.directory.name) / "similar-notes.sqlite3"))
        try:
            store.sync("PRJ-001", "csv", lambda: bundle)
            rows = AnalysisService(store, "PRJ-001", "csv").detect_blockers(CUTOFF)[
                "results"
            ]["blockers"]
            blockers = {row["task_id"]: row for row in rows}
            self.assertEqual(blockers["T-008"]["depends_on_task_ids"], ["T-004"])
            self.assertEqual(blockers["T-013"]["depends_on_task_ids"], ["T-004"])
            self.assertNotIn("T-013", blockers["T-008"]["affected_task_ids"])
            self.assertNotIn("T-008", blockers["T-013"]["affected_task_ids"])
        finally:
            store.close()

    def test_configured_project_identifier_stays_bound_across_tools(self):
        other = copy.deepcopy(self.bundle)
        for rows in other.values():
            for row in rows:
                row["Project ID"] = "PRJ-OTHER"
        for task in other["tasks"]:
            task["Owner"] = "Other project's owner"
        self.store.sync("PRJ-OTHER", "csv", lambda: other)
        calls = {
            "fetch_sprint_data": {},
            "detect_blockers": {},
            "risk_scorer": {},
            "trend_analysis": {"start_date": "2026-11-04"},
            "get_persistent_blockers": {},
            "query_sprint_history": {"task_id": "T-008"},
        }
        for name, arguments in calls.items():
            with self.subTest(tool=name):
                result = self.tools[name].invoke({"as_of_date": CUTOFF, **arguments})
                self.assertEqual(result["project_id"], "PRJ-001")
                self.assertEqual(result["source"], "csv")
                self.assertNotIn("Other project's owner", str(result))
