"""Actual Streamlit interaction and document-content checks, without API calls."""

import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from docx import Document
from langchain_core.messages import AIMessage
from pypdf import PdfReader
from streamlit.testing.v1 import AppTest

from project_status_agent.data.store import Store
from project_status_agent.data.validation import read_csv_bundle
from project_status_agent.reports.exports import markdown_bytes, pdf_bytes, word_bytes
from project_status_agent.reports.library import report_history
from project_status_agent.reports.workflow import run_report as backend_run
from project_status_agent.ui.charts import (
    period_changes,
    progress_comparison,
    sprint_tasks,
)

ROOT = Path(__file__).resolve().parents[1]


class OfflineReportModel:
    def __init__(self):
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        request = json.loads(messages[-1].content)
        return AIMessage(
            content=json.dumps(
                {
                    "summary": "Review the recorded blockers and confirm the proposed follow-up actions.",
                    "mitigations": [
                        {
                            "task_id": row["task_id"],
                            "proposed_action": "Ask the current owner to confirm the blocker and coordinate a next step.",
                            "success_check": "A dated update records resolution or the agreed escalation.",
                        }
                        for row in request["facts"]["project_risks"]["risks"]
                    ],
                }
            )
        )


class WebUiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = str(Path(self.directory.name) / "records.sqlite3")
        self.checkpoints = str(Path(self.directory.name) / "checkpoints.sqlite3")
        store = Store(self.database)
        try:
            store.sync(
                "PRJ-001",
                "notion",
                lambda: read_csv_bundle(ROOT / "sample_project_data"),
            )
        finally:
            store.close()
        self.model = OfflineReportModel()
        self.draft = backend_run(
            self.database,
            self.checkpoints,
            "PRJ-001",
            "notion",
            as_of_date="2026-11-04",
            model=self.model,
        )
        self.environment = {
            "DATABASE_PATH": self.database,
            "AGENT_CHECKPOINT_PATH": self.checkpoints,
            "PROJECT_ID": "PRJ-001",
        }

    def tearDown(self):
        self.directory.cleanup()

    def offline_run(self, *args, **kwargs):
        kwargs["model"] = self.model
        return backend_run(*args, **kwargs)

    @staticmethod
    def button(app, label):
        return next(button for button in app.button if button.label == label)

    def test_ui_displays_existing_report_and_download_controls(self):
        with patch.dict(os.environ, self.environment):
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertFalse(any(e.label == "Open by report ID" for e in app.expander))
            self.assertFalse(any(t.label == "Full report ID" for t in app.text_input))
            metrics = {metric.label: metric.value for metric in app.metric}
            self.assertEqual(metrics["Sprint tasks"], "12")
            self.assertEqual(metrics["Project risks"], "5")
            self.assertEqual(self.model.calls, 1)  # opening does not regenerate
            self.assertEqual(len(app.get("download_button")), 0)
            iframe = app.get("iframe")[0].proto.srcdoc
            for option in ["Download Word", "Download PDF", "Download Markdown"]:
                self.assertIn(option, iframe)
            self.assertIn("link.download=file.name", iframe)
            self.assertIn("Risks & mitigation", [tab.label for tab in app.tabs])
            self.assertEqual(
                [tab.label for tab in app.tabs], ["Overview", "Risks & mitigation"]
            )
            self.assertIn(
                "Source details & review history", [item.label for item in app.expander]
            )
            self.assertTrue(
                any(
                    "<ul" in element.value and "<li>" in element.value
                    for element in app.markdown
                )
            )
            self.assertEqual(len(app.get("vega_lite_chart")), 3)

    def test_owner_chart_does_not_regenerate_report(self):
        with patch.dict(os.environ, self.environment):
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
            app.radio[0].set_value("Workload by owner").run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.get("vega_lite_chart")), 3)
            self.assertEqual(self.model.calls, 1)

    def test_friendly_report_number_survives_revisions_and_reloads(self):
        first = report_history(self.checkpoints, "PRJ-001", "notion")[0]
        self.assertEqual(first["display_id"], "RPT-001")
        backend_run(
            self.database,
            self.checkpoints,
            "PRJ-001",
            "notion",
            report_id=self.draft["report_id"],
            decision="revise",
            version=1,
            feedback="Clarify the success checks.",
            model=self.model,
        )
        revised = report_history(self.checkpoints, "PRJ-001", "notion")[0]
        self.assertEqual(revised["display_id"], "RPT-001")
        self.assertEqual(revised["version"], 2)
        backend_run(
            self.database,
            self.checkpoints,
            "PRJ-001",
            "notion",
            as_of_date="2026-11-04",
            model=self.model,
        )
        rows = report_history(self.checkpoints, "PRJ-001", "notion")
        self.assertEqual({row["display_id"] for row in rows}, {"RPT-001", "RPT-002"})
        with patch.dict(os.environ, self.environment):
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
            picker = next(
                item for item in app.selectbox if item.label == "Select a report"
            )
            self.assertTrue(all(label.startswith("RPT-") for label in picker.options))
            self.assertNotIn("Technical details", [item.label for item in app.expander])

    def test_direct_report_link_overrides_previously_opened_report(self):
        other = backend_run(
            self.database,
            self.checkpoints,
            "PRJ-001",
            "notion",
            as_of_date="2026-11-04",
            model=self.model,
        )
        with patch.dict(os.environ, self.environment):
            app = AppTest.from_file(str(ROOT / "app.py"))
            app.session_state["active_report_id"] = other["report_id"]
            app.query_params["report_id"] = self.draft["report_id"]
            app.query_params["source"] = "notion"
            app.run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(
                app.session_state["active_report_id"], self.draft["report_id"]
            )
            picker = next(
                widget for widget in app.selectbox if widget.label == "Select a report"
            )
            self.assertEqual(picker.value, self.draft["report_id"])

    def test_report_selection_opens_immediately_and_updates_url(self):
        other = backend_run(
            self.database,
            self.checkpoints,
            "PRJ-001",
            "notion",
            as_of_date="2026-11-04",
            model=self.model,
        )
        with patch.dict(os.environ, self.environment):
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
            picker = next(
                widget for widget in app.selectbox if widget.label == "Select a report"
            )
            self.assertTrue(
                any(
                    "04 Nov 2026 · v1 · Awaiting review" in option
                    for option in picker.options
                )
            )
            picker.set_value(self.draft["report_id"]).run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(
                app.session_state["active_report_id"], self.draft["report_id"]
            )
            self.assertEqual(app.query_params["report_id"], self.draft["report_id"])
            self.assertFalse(
                any(block.value == self.draft["report_id"] for block in app.code)
            )
            self.assertNotIn(
                "Open selected report", [button.label for button in app.button]
            )
            self.assertNotEqual(other["report_id"], self.draft["report_id"])

    def test_report_link_opens_the_requested_report_without_generation(self):
        with patch.dict(os.environ, self.environment):
            app = AppTest.from_file(str(ROOT / "app.py"))
            app.query_params["report_id"] = self.draft["report_id"]
            app.query_params["source"] = "notion"
            app.run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(
                app.session_state["active_report_id"], self.draft["report_id"]
            )
            self.assertEqual(self.model.calls, 1)

    def test_charts_match_frozen_report_data(self):
        facts = self.draft["report"]["facts"]

        def rows(chart):
            return next(iter(chart.to_dict()["datasets"].values()))

        comparison = rows(progress_comparison(facts["project_trends"]))
        self.assertEqual(
            [row["Tasks"] for row in comparison if row["Status"] == "Done"], [4, 10]
        )
        changes = rows(period_changes(facts["project_trends"]))
        self.assertEqual(
            {row["Change"]: row["Tasks"] for row in changes},
            {"Newly completed": 6, "Newly blocked": 1, "Unchanged in progress": 2},
        )
        tasks = facts["sprint_progress"]["tasks"]
        self.assertEqual(len(rows(sprint_tasks(tasks, "Task status map"))), 12)
        self.assertEqual(
            sum(row["Tasks"] for row in rows(sprint_tasks(tasks, "Workload by owner"))),
            12,
        )

    def test_generate_and_approve_through_the_ui(self):
        with (
            patch.dict(os.environ, self.environment),
            patch(
                "project_status_agent.ui.reports.run_report",
                side_effect=self.offline_run,
            ),
        ):
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
            self.button(app, "Generate report").click().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(self.model.calls, 2)
            self.button(app, "Approve & save").click().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(
                any("approved and saved" in item.value for item in app.success)
            )
            self.assertEqual(self.model.calls, 2)  # approval does not call model
            self.assertNotIn("Approve & save", [button.label for button in app.button])
            store = Store(self.database)
            try:
                self.assertEqual(
                    store.db.execute(
                        "SELECT count(*) FROM approved_reports"
                    ).fetchone()[0],
                    1,
                )
            finally:
                store.close()

    def test_revision_feedback_and_new_version(self):
        with (
            patch.dict(os.environ, self.environment),
            patch(
                "project_status_agent.ui.reports.run_report",
                side_effect=self.offline_run,
            ),
        ):
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
            self.button(app, "Request revision").click().run(timeout=30)
            self.assertTrue(
                any("add feedback" in warning.value for warning in app.warning)
            )
            self.assertEqual(self.model.calls, 1)
            app.text_area[0].set_value("Make the success checks easier to verify.")
            self.button(app, "Request revision").click().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(self.model.calls, 2)
            history = report_history(self.checkpoints, "PRJ-001", "notion")
            self.assertEqual(history[0]["version"], 2)
            self.assertEqual(history[0]["status"], "awaiting_review")

    def test_reject_and_source_isolation(self):
        with (
            patch.dict(os.environ, self.environment),
            patch(
                "project_status_agent.ui.reports.run_report",
                side_effect=self.offline_run,
            ),
        ):
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
            self.button(app, "Reject report").click().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(
                any("was rejected" in warning.value for warning in app.warning)
            )
            self.assertEqual(report_history(self.checkpoints, "OTHER", "notion"), [])
            self.assertEqual(report_history(self.checkpoints, "PRJ-001", "csv"), [])

    def test_old_browser_form_cannot_approve_a_new_version(self):
        with (
            patch.dict(os.environ, self.environment),
            patch(
                "project_status_agent.ui.reports.run_report",
                side_effect=self.offline_run,
            ),
        ):
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
            stale_button = self.button(app, "Approve & save")
            backend_run(
                self.database,
                self.checkpoints,
                "PRJ-001",
                "notion",
                report_id=self.draft["report_id"],
                decision="revise",
                version=1,
                feedback="Clarify the success checks.",
                model=self.model,
            )
            stale_button.click().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            latest = backend_run(
                self.database,
                self.checkpoints,
                "PRJ-001",
                "notion",
                report_id=self.draft["report_id"],
                inspect_only=True,
            )
            self.assertEqual(latest["version"], 2)
            self.assertEqual(latest["status"], "awaiting_review")
            self.assertTrue(any("changed since" in item.value for item in app.warning))

    def test_word_export_contains_status_evidence_and_proposals(self):
        document = Document(
            BytesIO(word_bytes(self.draft["report"], "awaiting_review"))
        )
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn("Draft — not approved", text)
        self.assertIn("Proposed risk mitigation plan", text)
        self.assertIn("T-004-2026-11-04", text)
        self.assertIn("Synthetic-data", text)
        self.assertIn(
            "Draft — not approved", document.sections[0].footer.paragraphs[0].text
        )
        self.assertEqual(len(document.inline_shapes), 3)
        self.assertIn("Progress graphs", text)

    def test_pdf_contains_all_mitigation_evidence_and_pagination(self):
        reader = PdfReader(BytesIO(pdf_bytes(self.draft["report"], "awaiting_review")))
        text = "\n".join(page.extract_text() for page in reader.pages)
        self.assertGreaterEqual(len(reader.pages), 2)
        self.assertIn("Draft", text)
        self.assertIn("not approved", text)
        for proposal in self.draft["report"]["mitigations"]:
            self.assertIn(proposal["evidence_ids"][0], text)
        self.assertIn("No action has been assigned or executed", text)
        self.assertIn("Project status: before and after", text)
        self.assertGreaterEqual(sum(len(page.images) for page in reader.pages), 3)

    def test_approved_and_rejected_download_labels(self):
        report = self.draft["report"]
        approved = markdown_bytes(report, "saved").decode()
        rejected = markdown_bytes(report, "rejected").decode()
        self.assertIn("Version 1 | Approved", approved)
        self.assertIn("Rejected — not approved", rejected)
        self.assertEqual(approved.count("data:image/svg+xml;base64,"), 3)

    def test_qa_panel_investigates_and_restores_conversation(self):
        from test_agent import ScriptedModel, call

        from project_status_agent.agent.runner import run_agent

        model = ScriptedModel(
            [
                call("ask_human", {"question": "Which blocker period?"}),
                call("detect_blockers", {"as_of_date": "2026-11-04"}),
                AIMessage(content="T-004 is blocked; see snapshot evidence."),
            ]
        )

        def offline_agent(*args, **kwargs):
            kwargs["model"] = model
            return run_agent(*args, **kwargs)

        with (
            patch.dict(os.environ, self.environment),
            patch("project_status_agent.ui.chat.run_agent", side_effect=offline_agent),
        ):
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
            next(r for r in app.radio if r.label == "Workspace").set_value(
                "Ask the Agent"
            ).run()
            self.assertFalse(
                any(item.label == "Conversation" for item in app.selectbox)
            )
            self.assertEqual(len(app.chat_input), 1)
            app.chat_input[0].set_value("What is blocked?").run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(
                any("Which blocker period?" in item.value for item in app.info)
            )
            app.chat_input[0].set_value("Last week").run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any("T-004 is blocked" in m.value for m in app.markdown))
            self.assertFalse(any("detect_blockers" in m.value for m in app.markdown))
            self.assertFalse(
                any(
                    e.label == "Tool activity and investigation details"
                    for e in app.expander
                )
            )
            self.assertFalse(any(e.label == "Sources" for e in app.expander))
            self.assertEqual(len(model.requests), 3)
            app.run()
            self.assertEqual(len(model.requests), 3)
            model.responses.append(
                AIMessage(
                    content="Arjun owns T-004, based on the previous blocker evidence."
                )
            )
            app.chat_input[0].set_value("Who owns T-004?").run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(model.requests), 4)
            self.assertTrue(any("Arjun owns T-004" in m.value for m in app.markdown))
            self.assertFalse(any("couldn't finish" in w.value for w in app.warning))

    def test_qa_sync_denial_uses_saved_evidence_and_discloses_failure(self):
        from test_agent import TODAY, ScriptedModel, call

        from project_status_agent.agent.runner import run_agent

        model = ScriptedModel(
            [
                call("sync_project_data", {"observed_on": TODAY}),
                call("detect_blockers", {"as_of_date": "2026-11-04"}),
                AIMessage(
                    content="Sync was denied. Using saved snapshots: T-004 is blocked."
                ),
            ]
        )

        def offline_agent(*args, **kwargs):
            kwargs["model"] = model
            return run_agent(*args, **kwargs)

        with (
            patch.dict(os.environ, self.environment),
            patch("project_status_agent.ui.chat.run_agent", side_effect=offline_agent),
        ):
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
            next(r for r in app.radio if r.label == "Workspace").set_value(
                "Ask the Agent"
            ).run()
            app.chat_input[0].set_value("Please sync fresh Notion data").run(timeout=30)
            self.button(app, "Cancel").click().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(
                any("wasn't refreshed" in item.value for item in app.warning)
            )
            self.assertTrue(
                any("Sync was denied" in item.value for item in app.markdown)
            )

    def test_qa_timeout_shows_retry_without_duplicating_question(self):
        import httpx
        from openai import APITimeoutError
        from test_agent import ScriptedModel, call

        from project_status_agent.agent.runner import run_agent, saved_messages

        class RecoveringModel(ScriptedModel):
            def invoke(self, messages):
                if not self.requests:
                    self.requests.append(messages)
                    raise APITimeoutError(
                        request=httpx.Request("POST", "https://example.com")
                    )
                return super().invoke(messages)

        model = RecoveringModel(
            [
                call("detect_blockers", {"as_of_date": "2026-11-04"}),
                AIMessage(content="T-004 is blocked."),
            ]
        )

        def offline_agent(*args, **kwargs):
            kwargs["model"] = model
            return run_agent(*args, **kwargs)

        with (
            patch.dict(os.environ, self.environment),
            patch("project_status_agent.ui.chat.run_agent", side_effect=offline_agent),
        ):
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
            next(r for r in app.radio if r.label == "Workspace").set_value(
                "Ask the Agent"
            ).run()
            app.chat_input[0].set_value("What is blocked?").run(timeout=30)
            self.assertEqual(len(model.requests), 1)
            self.assertTrue(any("longer than expected" in w.value for w in app.warning))
            self.button(app, "Retry").click().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertFalse(any(b.label == "Retry" for b in app.button))
            self.assertTrue(any("T-004 is blocked" in m.value for m in app.markdown))
            messages = saved_messages(
                self.checkpoints, app.query_params["agent_thread"]
            )
            self.assertEqual(sum(m.type == "human" for m in messages), 1)

    def test_qa_starter_prompt_and_plain_errors(self):
        from project_status_agent.integrations.http import ServiceError

        with (
            patch.dict(os.environ, self.environment),
            patch(
                "project_status_agent.ui.chat.run_agent",
                side_effect=ServiceError("HTTP 401 private-debug-value"),
            ) as backend,
        ):
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
            next(r for r in app.radio if r.label == "Workspace").set_value(
                "Ask the Agent"
            ).run()
            self.button(app, "Summarize this sprint").click().run(timeout=30)
            self.assertEqual(
                backend.call_args.kwargs["question"], "Summarize this sprint"
            )
            self.assertTrue(any("couldn’t answer" in e.value for e in app.error))
            self.assertFalse(
                any("401" in e.value or "private-debug" in e.value for e in app.error)
            )
            self.assertEqual(len(app.json), 0)

    def test_report_presentation_uses_latest_report_dates_and_clean_labels(self):
        store = Store(self.database)
        try:
            store.sync(
                "PRJ-001",
                "notion",
                lambda: read_csv_bundle(ROOT / "sample_project_data_nov10"),
            )
        finally:
            store.close()
        draft = backend_run(
            self.database,
            self.checkpoints,
            "PRJ-001",
            "notion",
            as_of_date="2026-11-10",
            start_date="2026-11-03",
            model=self.model,
        )
        with patch.dict(os.environ, self.environment):
            app = AppTest.from_file(str(ROOT / "app.py"))
            app.query_params["report_id"] = draft["report_id"]
            app.run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.title[0].value, "Project Pulse Overview")
            self.assertEqual(
                sum(
                    "Synthetic-data demonstration" in item.value for item in app.caption
                ),
                1,
            )
            captions = [item.value for item in app.caption]
            self.assertTrue(any("2026-11-02 → 2026-11-10" in item for item in captions))
            for removed in [
                "Generation uses Nebius",
                "private API keys stay",
                "The comparison uses the closest",
                "Word for editing",
            ]:
                self.assertFalse(any(removed in item for item in captions))
            self.assertNotIn("Technical details", [item.label for item in app.expander])
            self.assertTrue(
                any("Sprint task progress" in item.value for item in app.markdown)
            )
            sidebar = list(app.sidebar)
            project_index = next(
                i
                for i, element in enumerate(sidebar)
                if getattr(element, "value", None) == "Project: PRJ-001"
            )
            source_index = next(
                i
                for i, element in enumerate(sidebar)
                if getattr(element, "label", None) == "Data source"
            )
            self.assertLess(project_index, source_index)

    def test_fresh_qa_recovers_missing_tool_without_showing_unsupported_answer(self):
        from test_agent import ScriptedModel, call

        from project_status_agent.agent.runner import run_agent

        model = ScriptedModel(
            [
                AIMessage(content="UNSUPPORTED_PLACEHOLDER"),
                call("fetch_sprint_data", {"as_of_date": "2026-11-04"}),
                AIMessage(
                    content="Sprint 3 has 12 tasks based on the retrieved records."
                ),
            ]
        )

        def offline_agent(*args, **kwargs):
            kwargs["model"] = model
            return run_agent(*args, **kwargs)

        with (
            patch.dict(os.environ, self.environment),
            patch("project_status_agent.ui.chat.run_agent", side_effect=offline_agent),
        ):
            app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
            next(r for r in app.radio if r.label == "Workspace").set_value(
                "Ask the Agent"
            ).run()
            app.chat_input[0].set_value("Summarize Sprint 3").run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(model.requests), 3)
            self.assertTrue(any("12 tasks" in m.value for m in app.markdown))
            self.assertFalse(
                any("UNSUPPORTED_PLACEHOLDER" in m.value for m in app.markdown)
            )


if __name__ == "__main__":
    unittest.main()
