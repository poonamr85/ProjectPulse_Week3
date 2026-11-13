"""Verify project facts and tool behavior without network or model calls."""

import copy
import tempfile
import unittest
from pathlib import Path

from project_status_agent.agent.tools import create_tools
from project_status_agent.data.analysis import AnalysisService
from project_status_agent.data.store import Store
from project_status_agent.data.validation import read_csv_bundle
from project_status_agent.integrations.http import ServiceError

ROOT = Path(__file__).resolve().parents[1]
AS_OF = "2026-11-04"


class AnalysisToolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "project.sqlite3")
        self.bundle = read_csv_bundle(ROOT / "sample_project_data")
        self.store = Store(self.path)
        self.store.sync("PRJ-001", "csv", lambda: copy.deepcopy(self.bundle))
        self.analysis = AnalysisService(self.store, "PRJ-001", "csv")

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_current_sprint_and_historical_cutoff(self):
        current = self.analysis.fetch_sprint_data(AS_OF)["results"]
        self.assertEqual(current["task_count"], 12)
        self.assertEqual(
            current["status_counts"],
            {"Blocked": 3, "In progress": 5, "Done": 2, "To do": 2},
        )
        self.assertEqual(current["completion_percentage"], 16.7)
        past = self.analysis.fetch_sprint_data("2026-10-16")["results"]
        self.assertEqual(past["sprint"]["Sprint ID"], "SPR-01")
        self.assertEqual(past["status_counts"]["Done"], 3)
        self.assertTrue(
            all(row["observed_on"] <= "2026-10-16" for row in past["tasks"])
        )

    def test_explicit_blockers_and_dependencies(self):
        rows = self.analysis.detect_blockers(AS_OF)["results"]["blockers"]
        by_id = {row["task_id"]: row for row in rows}
        self.assertEqual(set(by_id), {"T-004", "T-008", "T-013"})
        self.assertEqual(by_id["T-004"]["affected_task_ids"], ["T-008", "T-013"])
        self.assertNotIn("T-006", by_id)

    def test_risk_severity_and_overdue_tasks(self):
        result = self.analysis.risk_scorer(AS_OF)["results"]
        risks = {row["task_id"]: row for row in result["risks"]}
        self.assertEqual(result["overall_flag"], "Critical")
        self.assertEqual(risks["T-004"]["severity"], "Critical")
        self.assertEqual(risks["T-013"]["severity"], "Critical")
        self.assertEqual(risks["T-006"]["severity"], "High")
        overdue = {
            task_id
            for task_id, row in risks.items()
            if "Incomplete and past current due date" in row["reasons"]
        }
        self.assertEqual(overdue, {"T-004", "T-006", "T-008", "T-011"})
        self.assertNotIn("T-015", risks)  # completed work is not a current risk

    def test_persistent_blockers_count_sprints_not_runs(self):
        self.store.sync("PRJ-001", "csv", lambda: copy.deepcopy(self.bundle))
        rows = self.analysis.get_persistent_blockers(AS_OF)["results"]["blockers"]
        self.assertEqual({row["task_id"] for row in rows}, {"T-004", "T-008"})
        early = self.analysis.get_persistent_blockers("2026-10-16")["results"]
        self.assertEqual(early["count"], 0)

    def test_recovered_persistent_blocker_is_optional(self):
        bundle = copy.deepcopy(self.bundle)
        for task in bundle["tasks"]:
            if task["Task ID"] == "T-004":
                task["Status"] = "Done"
                task["Status Since"] = AS_OF
        for row in bundle["history"]:
            if row["Task ID"] == "T-004" and row["Snapshot Date"] == AS_OF:
                row["Status"] = "Done"
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / "recovered.sqlite3"))
            try:
                store.sync("PRJ-001", "csv", lambda: bundle)
                service = AnalysisService(store, "PRJ-001", "csv")
                current = service.get_persistent_blockers(AS_OF)["results"]["blockers"]
                historical = service.get_persistent_blockers(AS_OF, False)["results"][
                    "blockers"
                ]
                self.assertNotIn("T-004", {row["task_id"] for row in current})
                self.assertIn("T-004", {row["task_id"] for row in historical})
            finally:
                store.close()

    def test_trend_boundaries_and_distinct_observation_dates(self):
        dated = self.analysis.trend_analysis(AS_OF, start_date="2026-10-30")["results"]
        counted = self.analysis.trend_analysis(AS_OF, last_n_snapshots=3)["results"]
        self.assertEqual(dated["newly_observed_done_ids"], ["T-015", "T-016"])
        self.assertEqual(dated, counted)
        self.assertEqual(
            {row["task_id"] for row in dated["unchanged_in_progress"]},
            {"T-006", "T-011"},
        )
        approximate = self.analysis.trend_analysis(AS_OF, start_date="2026-10-28")
        self.assertEqual(
            approximate["results"]["start_observation_dates"], ["2026-10-23"]
        )
        self.assertTrue(any("approximate" in note for note in approximate["warnings"]))

    def test_pagination_and_sprint_filters(self):
        first = self.analysis.query_sprint_history(AS_OF, task_id="T-004", limit=2)[
            "results"
        ]
        second = self.analysis.query_sprint_history(
            AS_OF, task_id="T-004", limit=2, offset=first["next_offset"]
        )["results"]
        self.assertEqual(first["total"], 6)
        first_ids = {row["evidence"]["snapshot_id"] for row in first["observations"]}
        self.assertTrue(
            first_ids.isdisjoint(
                row["evidence"]["snapshot_id"] for row in second["observations"]
            )
        )
        sprint = self.analysis.query_sprint_history(
            AS_OF, task_id="T-004", sprint_id="SPR-02"
        )["results"]
        self.assertEqual(sprint["total"], 2)

    def test_missing_history_and_failed_sync_are_disclosed(self):
        early = self.analysis.query_sprint_history("2026-10-01")
        self.assertEqual(early["coverage"]["tasks_with_observations"], 0)
        self.assertTrue(
            any("coverage is incomplete" in note for note in early["warnings"])
        )
        with self.assertRaises(ServiceError):
            self.store.sync(
                "PRJ-001", "csv", lambda: (_ for _ in ()).throw(ServiceError("offline"))
            )
        result = self.analysis.fetch_sprint_data(AS_OF)
        self.assertTrue(
            any("Latest sync did not complete" in note for note in result["warnings"])
        )

    def test_invalid_inputs(self):
        for operation in (
            lambda: self.analysis.fetch_sprint_data("2026-11-04", "missing"),
            lambda: self.analysis.fetch_sprint_data("invalid"),
            lambda: self.analysis.trend_analysis(AS_OF),
            lambda: self.analysis.trend_analysis(AS_OF, start_date=AS_OF),
            lambda: self.analysis.trend_analysis(AS_OF, last_n_snapshots=10),
            lambda: self.analysis.query_sprint_history(AS_OF, limit=101),
            lambda: self.analysis.query_sprint_history(AS_OF, task_id="missing"),
            lambda: self.analysis.get_persistent_blockers(AS_OF, minimum_sprints=1),
        ):
            with self.assertRaises(ValueError):
                operation()

    def test_source_and_project_isolation(self):
        for service in (
            AnalysisService(self.store, "OTHER", "csv"),
            AnalysisService(self.store, "PRJ-001", "notion"),
        ):
            with self.assertRaises(ValueError):
                service.fetch_sprint_data(AS_OF)

    def test_memory_context_requires_matching_local_evidence(self):
        class FakeMemory:
            project = "PRJ-001"

            def search(self, query, source, as_of_date, limit):
                def match(snapshot, task, project="PRJ-001"):
                    return {
                        "id": snapshot,
                        "memory": "context",
                        "metadata": {
                            "snapshot_id": snapshot,
                            "task_id": task,
                            "project_id": project,
                            "source": source,
                        },
                    }

                return [
                    match("T-004-2026-10-30", "T-004"),
                    match("T-004-2026-11-04", "T-004"),
                    match("T-004-2026-10-30", "T-008"),
                    match("unknown", "T-004"),
                    match("T-004-2026-10-30", "T-004", "OTHER"),
                ]

        service = AnalysisService(self.store, "PRJ-001", "csv", FakeMemory())
        result = service.search_project_memory("payment", "2026-10-30")
        self.assertEqual(len(result["results"]["matches"]), 1)
        with self.assertRaises(ValueError):
            self.analysis.search_project_memory("payment", AS_OF)

    def test_real_langchain_wrappers_invoke_and_schema(self):
        tools = {tool.name: tool for tool in create_tools(self.path, "PRJ-001", "csv")}
        self.assertEqual(len(tools), 8)
        for name in (
            "fetch_sprint_data",
            "detect_blockers",
            "risk_scorer",
            "get_persistent_blockers",
            "query_sprint_history",
        ):
            result = tools[name].invoke({"as_of_date": AS_OF})
            self.assertEqual(result["project_id"], "PRJ-001")
            self.assertIn("as_of_date", tools[name].args)
        self.assertIsInstance(tools["fetch_sprint_data"].invoke({}), str)
        self.assertIsInstance(
            tools["fetch_sprint_data"].invoke({"as_of_date": "invalid"}), str
        )
        self.assertIsInstance(
            tools["sync_project_data"].invoke({"observed_on": "invalid"}), str
        )
        self.assertIn(
            "provide", tools["trend_analysis"].invoke({"as_of_date": AS_OF}).lower()
        )

    def test_memory_wrapper_returns_context_and_actionable_service_error(self):
        class FakeMemory:
            project = "PRJ-001"
            unavailable = False

            def search(self, query, source, as_of_date, limit):
                if self.unavailable:
                    raise ServiceError(
                        "Mem0 unavailable; use structured history or retry later."
                    )
                return [
                    {
                        "id": "memory-1",
                        "memory": "Provider credentials pending.",
                        "metadata": {
                            "snapshot_id": "T-004-2026-10-30",
                            "task_id": "T-004",
                            "project_id": "PRJ-001",
                            "source": source,
                        },
                    }
                ]

        memory = FakeMemory()
        tools = {
            tool.name: tool
            for tool in create_tools(
                self.path, "PRJ-001", "csv", memory_factory=lambda: memory
            )
        }
        arguments = {"query": "Why is payment blocked?", "as_of_date": AS_OF}
        result = tools["search_project_memory"].invoke(arguments)
        self.assertEqual(len(result["results"]["matches"]), 1)
        memory.unavailable = True
        self.assertIn(
            "Mem0 unavailable", tools["search_project_memory"].invoke(arguments)
        )

    def test_sync_tool_saves_local_snapshot_and_does_not_publish_memory(self):
        tools = {
            tool.name: tool
            for tool in create_tools(
                self.path,
                "PRJ-001",
                "csv",
                sync_fetcher=lambda: copy.deepcopy(self.bundle),
            )
        }
        result = tools["sync_project_data"].invoke({"observed_on": "2026-11-05"})
        self.assertEqual(result["results"]["synced"]["history"], 140)
        self.assertEqual(
            self.store.summary("PRJ-001", "csv")["memory_delivery"], {"pending": 140}
        )


if __name__ == "__main__":
    unittest.main()
