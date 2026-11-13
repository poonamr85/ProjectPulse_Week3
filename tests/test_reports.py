"""Report facts, mitigation evidence, revision, and durable approval tests."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from langchain_core.messages import AIMessage

from project_status_agent.agent.tools import create_tools
from project_status_agent.data.store import Store
from project_status_agent.data.validation import read_csv_bundle
from project_status_agent.reports.content import (
    collect_evidence,
    report_hash,
    save_approved_report,
    summary_points,
    validate_report,
)
from project_status_agent.reports.workflow import run_report

ROOT = Path(__file__).resolve().parents[1]


class ReportModel:
    """Return schema-shaped suggestions from the provided risk facts."""

    def __init__(self, invalid=False):
        self.requests = []
        self.invalid = invalid

    def invoke(self, messages):
        self.requests.append(messages)
        if self.invalid:
            return AIMessage(content="not JSON")
        request = json.loads(messages[-1].content)
        narrative = {
            "summary": "Payment dependencies and overdue work need owner review.",
            "mitigations": [
                {
                    "task_id": risk["task_id"],
                    "proposed_action": "Ask the task owner to confirm the recorded issue and agree the next step.",
                    "success_check": "A new dated update records the agreed next step or resolution.",
                }
                for risk in request["facts"]["project_risks"]["risks"]
            ],
        }
        return AIMessage(content=json.dumps(narrative))


class ReportTests(unittest.TestCase):
    def test_summary_bullets_preserve_existing_prose_and_task_ids(self):
        self.assertEqual(
            summary_points(
                "T-004 is blocked. T-008 depends on it.\n- Follow up with the owner."
            ),
            ["T-004 is blocked.", "T-008 depends on it.", "Follow up with the owner."],
        )

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "records.sqlite3")
        self.checkpoints = str(Path(self.directory.name) / "checkpoints.sqlite3")
        self.store = Store(self.path)
        bundle = read_csv_bundle(ROOT / "sample_project_data")
        self.store.sync("PRJ-001", "csv", lambda: copy.deepcopy(bundle))
        self.tools = create_tools(self.path, "PRJ-001", "csv")
        self.evidence = collect_evidence(
            self.tools, "PRJ-001", "csv", "2026-10-28", "2026-11-04"
        )

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def run_report(self, **kwargs):
        return run_report(
            self.path,
            self.checkpoints,
            "PRJ-001",
            "csv",
            model=kwargs.pop("model", ReportModel()),
            tools=self.tools,
            **kwargs,
        )

    def draft(self):
        return self.run_report(as_of_date="2026-11-04")

    def test_checked_draft_has_mitigations_and_disclosure(self):
        result = self.draft()
        self.assertEqual(result["status"], "awaiting_review")
        self.assertEqual(result["validation_issues"], [])
        report = result["report"]
        self.assertEqual(report["facts"]["sprint_progress"]["task_count"], 12)
        self.assertEqual(len(report["mitigations"]), 5)
        self.assertIn("Synthetic-data", result["markdown"])
        self.assertIn("No action has been assigned or executed", result["markdown"])
        self.assertTrue(all(item["suggested_owner"] for item in report["mitigations"]))
        self.assertEqual(
            report["facts"]["project_trends"]["start_observation_dates"], ["2026-10-23"]
        )

    def test_approval_survives_restart_and_is_idempotent(self):
        draft = self.draft()
        inspected = self.run_report(report_id=draft["report_id"], inspect_only=True)
        self.assertEqual(inspected["status"], "awaiting_review")
        for _ in range(2):
            approved = self.run_report(
                report_id=draft["report_id"], decision="approve", version=1
            )
            self.assertEqual(approved["status"], "saved")
        row = self.store.db.execute(
            "SELECT count(*), markdown FROM approved_reports"
        ).fetchone()
        self.assertEqual(row[0], 1)
        self.assertIn("Approved", row[1])
        self.assertNotIn(
            "'sent'", str(self.store.summary("PRJ-001", "csv")["memory_delivery"])
        )

    def test_rejection_does_not_save(self):
        draft = self.draft()
        rejected = self.run_report(
            report_id=draft["report_id"], decision="reject", version=1
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(
            self.store.db.execute(
                "SELECT count(*) FROM sqlite_master WHERE name='approved_reports'"
            ).fetchone()[0],
            0,
        )

    def test_revision_requires_new_approval(self):
        draft = self.draft()
        model = ReportModel()
        revised = self.run_report(
            report_id=draft["report_id"],
            decision="revise",
            version=1,
            feedback="Make proposed success checks easier to verify.",
            model=model,
        )
        self.assertEqual(revised["status"], "awaiting_review")
        self.assertEqual(revised["version"], 2)
        request = json.loads(model.requests[0][-1].content)
        self.assertIn("easier to verify", request["review_feedback"])
        with self.assertRaisesRegex(ValueError, "stale"):
            self.run_report(report_id=draft["report_id"], decision="approve", version=1)
        approved = self.run_report(
            report_id=draft["report_id"], decision="approve", version=2
        )
        self.assertEqual(approved["status"], "saved")

    def test_review_requires_the_displayed_content_hash(self):
        draft = self.draft()
        with self.assertRaisesRegex(ValueError, "content changed"):
            self.run_report(
                report_id=draft["report_id"],
                decision="approve",
                version=1,
                expected_hash="stale-content-hash",
            )
        self.assertEqual(
            self.run_report(report_id=draft["report_id"], inspect_only=True)["status"],
            "awaiting_review",
        )

    def test_bad_generation_is_bounded_and_never_approved(self):
        model = ReportModel(invalid=True)
        result = self.run_report(as_of_date="2026-11-04", model=model)
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(len(model.requests), 2)
        self.assertTrue(result["validation_issues"])

    def test_output_limit_stops_without_schema_retry_or_approval(self):
        class TruncatedModel(ReportModel):
            def invoke(self, messages):
                self.requests.append(messages)
                return AIMessage(content="", response_metadata={"finish_reason": "length"})

        model = TruncatedModel()
        result = self.run_report(as_of_date="2026-11-04", model=model)
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(len(model.requests), 1)
        self.assertIn("output limit", result["validation_issues"][0])

    def test_empty_report_is_bounded_and_has_specific_error(self):
        class EmptyModel(ReportModel):
            def invoke(self, messages):
                self.requests.append(messages)
                return AIMessage(content="")

        model = EmptyModel()
        result = self.run_report(as_of_date="2026-11-04", model=model)
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(len(model.requests), 2)
        self.assertIn("empty report", result["validation_issues"][0])

    def test_application_attaches_verified_references_and_model_does_not_supply_them(self):
        model = ReportModel()
        result = self.run_report(as_of_date="2026-11-04", model=model)
        self.assertEqual(result["status"], "awaiting_review")
        request = json.loads(model.requests[0][-1].content)
        proposal_schema = request["schema"]["$defs"]["MitigationProposal"]
        self.assertNotIn("evidence_ids", proposal_schema["properties"])
        risks = {
            risk["task_id"]: risk
            for risk in result["report"]["facts"]["project_risks"]["risks"]
        }
        for proposal in result["report"]["mitigations"]:
            self.assertEqual(
                proposal["evidence_ids"],
                [risks[proposal["task_id"]]["evidence"]["snapshot_id"]],
            )

    def test_forged_counts_risk_or_evidence_are_rejected(self):
        draft = self.draft()["report"]
        for edit in (
            lambda r: r["facts"]["sprint_progress"].update({"task_count": 999}),
            lambda r: r["facts"]["project_risks"]["risks"][0].update(
                {"severity": "Low"}
            ),
            lambda r: r["mitigations"][0].update({"evidence_ids": ["not-real"]}),
            lambda r: r["mitigations"][0].update({"suggested_owner": "Invented owner"}),
            lambda r: r.update({"mitigations": []}),
            lambda r: r.update({"summary": "Please ask T-999 to fix the issue."}),
        ):
            report = copy.deepcopy(draft)
            edit(report)
            self.assertTrue(validate_report(report, self.evidence))

    def test_future_evidence_is_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["risk_scorer"]["results"]["risks"][0]["evidence"]["observed_on"] = (
            "2026-11-05"
        )
        draft = self.draft()["report"]
        draft["facts"]["project_risks"] = copy.deepcopy(
            evidence["risk_scorer"]["results"]
        )
        self.assertTrue(
            any("cutoff" in issue for issue in validate_report(draft, evidence))
        )

    def test_report_change_after_approval_hash_is_rejected(self):
        report = self.draft()["report"]
        approved_hash = report_hash(report)
        report["summary"] = "Changed after review."
        with self.assertRaisesRegex(ValueError, "changed after approval"):
            save_approved_report(self.path, report, approved_hash)

    def test_missing_feedback_and_cross_project_access_are_rejected(self):
        draft = self.draft()
        with self.assertRaisesRegex(ValueError, "feedback"):
            self.run_report(report_id=draft["report_id"], decision="revise", version=1)
        with self.assertRaisesRegex(ValueError, "another project"):
            run_report(
                self.path,
                self.checkpoints,
                "OTHER",
                "csv",
                report_id=draft["report_id"],
                inspect_only=True,
            )

    def test_report_receives_same_data_contract_and_source_limitations(self):
        from project_status_agent.data.contract import (
            DATA_LIMITATIONS,
            contract_instructions,
        )

        model = ReportModel()
        result = self.run_report(as_of_date="2026-11-04", model=model)
        self.assertEqual(result["status"], "awaiting_review")
        self.assertIn(contract_instructions(), model.requests[0][0].content)
        self.assertIn("tools are not available here", model.requests[0][0].content)
        self.assertTrue(set(DATA_LIMITATIONS) <= set(result["report"]["limitations"]))


if __name__ == "__main__":
    unittest.main()
