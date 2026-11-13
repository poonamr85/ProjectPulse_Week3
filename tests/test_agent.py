"""Exercise real graph routing, bounded recovery, and durable human handoff."""

import copy
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from project_status_agent.agent.runner import run_agent
from project_status_agent.agent.tools import create_tools
from project_status_agent.agent.workflow import (
    build_agent,
    count_reusable_evidence,
    initial_state,
)
from project_status_agent.data.store import Store
from project_status_agent.data.validation import read_csv_bundle

ROOT = Path(__file__).resolve().parents[1]
AS_OF = "2026-11-04"
TODAY = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def call(name, arguments, identifier="call-1"):
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": arguments, "id": identifier}]
    )


class ScriptedModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def bind_tools(self, tools, tool_choice):
        self.tools = tools
        return self

    def invoke(self, messages):
        self.requests.append(messages)
        if not self.responses:
            raise AssertionError("Unexpected additional model request")
        return self.responses.pop(0)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "data.sqlite3")
        self.checkpoints = str(Path(self.directory.name) / "checkpoints.sqlite3")
        store = Store(self.path)
        try:
            bundle = read_csv_bundle(ROOT / "sample_project_data")
            store.sync("PRJ-001", "csv", lambda: copy.deepcopy(bundle))
        finally:
            store.close()
        self.tools = create_tools(self.path, "PRJ-001", "csv")

    def tearDown(self):
        self.directory.cleanup()

    def execute(self, model, **limits):
        graph = build_agent(model, self.tools, "PRJ-001", "csv", **limits)
        return graph.invoke(initial_state("What is blocked?", "PRJ-001", "csv", AS_OF))

    def test_model_selects_tools_then_uses_observations(self):
        model = ScriptedModel(
            [
                call("detect_blockers", {"as_of_date": AS_OF}),
                call("get_persistent_blockers", {"as_of_date": AS_OF}, "call-2"),
                AIMessage(
                    content="T-004 and T-008 have blocked observations across multiple sprints."
                ),
            ]
        )
        result = self.execute(model)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["successful_tools"], 2)
        self.assertEqual(result["model_calls"], 3)
        self.assertEqual(result["trace"][0]["tool"], "detect_blockers")
        self.assertEqual(model.requests[1][-1].type, "tool")

    def test_unknown_tool_can_be_corrected(self):
        result = self.execute(
            ScriptedModel(
                [
                    call("unknown", {}),
                    call("fetch_sprint_data", {"as_of_date": AS_OF}, "call-2"),
                    AIMessage(content="There are 12 current-sprint tasks."),
                ]
            )
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["tool_errors"], 1)
        self.assertEqual(result["trace"][0]["outcome"], "error")

    def test_schema_error_can_be_corrected(self):
        result = self.execute(
            ScriptedModel(
                [
                    call("fetch_sprint_data", {}),
                    call("fetch_sprint_data", {"as_of_date": AS_OF}, "call-2"),
                    AIMessage(content="There are 12 current-sprint tasks."),
                ]
            )
        )
        self.assertEqual(result["tool_errors"], 1)
        self.assertEqual(result["status"], "completed")

    def test_repeated_failure_hands_off(self):
        model = ScriptedModel([call("unknown", {}, f"call-{i}") for i in range(3)])
        result = self.execute(model)
        self.assertEqual(result["status"], "needs_human")
        self.assertEqual(result["model_calls"], 3)
        self.assertEqual(result["tool_errors"], 3)

    def test_model_budget_stops_loop(self):
        result = self.execute(
            ScriptedModel(
                [
                    call("fetch_sprint_data", {"as_of_date": AS_OF}),
                ]
            ),
            max_model_calls=1,
        )
        self.assertEqual(result["status"], "needs_human")

    def test_no_evidence_is_not_claimed_success(self):
        model = ScriptedModel(
            [
                AIMessage(content="Everything is fine."),
                AIMessage(content="Still no evidence."),
            ]
        )
        result = self.execute(model)
        self.assertEqual(result["status"], "needs_human")
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(result["evidence_retries"], 1)
        self.assertFalse(any(m.type == "ai" for m in result["messages"]))

    def test_future_evidence_is_rejected(self):
        result = self.execute(
            ScriptedModel(
                [
                    call("fetch_sprint_data", {"as_of_date": "2026-11-05"}),
                ]
            ),
            max_model_calls=1,
        )
        self.assertEqual(result["successful_tools"], 0)
        self.assertIn("cutoff", result["trace"][0]["error"])

    def test_malformed_json_selection_can_recover(self):
        malformed = AIMessage(
            content="",
            invalid_tool_calls=[
                {
                    "name": "fetch_sprint_data",
                    "args": "{bad",
                    "id": "bad-call",
                    "error": "invalid JSON",
                }
            ],
        )
        result = self.execute(
            ScriptedModel(
                [
                    malformed,
                    call("fetch_sprint_data", {"as_of_date": AS_OF}),
                    AIMessage(content="There are 12 sprint tasks."),
                ]
            )
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["tool_errors"], 1)

    def test_durable_pause_inspect_and_resume_after_reconstruction(self):
        executions = []

        @tool
        def sync_project_data(observed_on: str) -> dict:
            """A fake sync to verify that execution waits for approval."""
            executions.append(observed_on)
            return {"synced": True}

        first = run_agent(
            self.path,
            self.checkpoints,
            "PRJ-001",
            "csv",
            question="Refresh project data",
            as_of_date=TODAY,
            thread_id="approval-test",
            model=ScriptedModel([call("sync_project_data", {"observed_on": TODAY})]),
            tools=[sync_project_data],
        )
        self.assertEqual(first["status"], "awaiting_approval")
        self.assertEqual(executions, [])
        inspected = run_agent(
            self.path,
            self.checkpoints,
            "PRJ-001",
            "csv",
            thread_id="approval-test",
            inspect_only=True,
        )
        self.assertEqual(inspected["status"], "awaiting_approval")
        resumed = run_agent(
            self.path,
            self.checkpoints,
            "PRJ-001",
            "csv",
            thread_id="approval-test",
            decision=True,
            model=ScriptedModel([AIMessage(content="The approved sync completed.")]),
            tools=[sync_project_data],
        )
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(executions, [TODAY])
        final = run_agent(
            self.path,
            self.checkpoints,
            "PRJ-001",
            "csv",
            thread_id="approval-test",
            inspect_only=True,
        )
        self.assertEqual(final["status"], "completed")

    def test_denial_does_not_execute_sync(self):
        graph = build_agent(
            ScriptedModel(
                [
                    call("sync_project_data", {"observed_on": TODAY}),
                    call("fetch_sprint_data", {"as_of_date": AS_OF}, "call-2"),
                    AIMessage(content="Used saved data; sync was denied."),
                ]
            ),
            self.tools,
            "PRJ-001",
            "csv",
            checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": "deny"}}
        graph.invoke(initial_state("Refresh", "PRJ-001", "csv", AS_OF), config)
        from langgraph.types import Command

        result = graph.invoke(Command(resume=False), config)
        self.assertEqual(result["status"], "completed")
        self.assertIn("denied", result["trace"][0]["error"])

    def test_clarification_resume_and_followup_keep_context(self):
        args = (self.path, self.checkpoints, "PRJ-001", "csv")
        paused = run_agent(
            *args,
            question="Compare progress",
            as_of_date=AS_OF,
            model=ScriptedModel([call("ask_human", {"question": "Which period?"})]),
            tools=self.tools,
        )
        self.assertEqual(paused["approval_request"]["action"], "clarify")
        thread = paused["thread_id"]
        with self.assertRaisesRegex(ValueError, "pending human approval"):
            run_agent(*args, thread_id=thread, question="Another question")
        with self.assertRaisesRegex(ValueError, "clarification"):
            run_agent(*args, thread_id=thread, decision=True)
        model = ScriptedModel(
            [
                call("detect_blockers", {"as_of_date": AS_OF}),
                AIMessage(content="T-004 is blocked."),
            ]
        )
        result = run_agent(
            *args,
            thread_id=thread,
            clarification="Last week",
            model=model,
            tools=self.tools,
        )
        self.assertEqual(result["status"], "completed")
        self.assertTrue(any(m.content == "Last week" for m in model.requests[0]))
        followup = ScriptedModel(
            [
                call("get_persistent_blockers", {"as_of_date": AS_OF}),
                AIMessage(content="T-004 and T-008 persist."),
            ]
        )
        result = run_agent(
            *args,
            thread_id=thread,
            question="Which persisted?",
            model=followup,
            tools=self.tools,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(len(result["tool_trace"]), 1)
        self.assertTrue(
            any(m.content == "T-004 is blocked." for m in followup.requests[0])
        )
        restored = run_agent(*args, thread_id=thread, inspect_only=True)
        self.assertEqual(restored["answer"], result["answer"])
        with self.assertRaisesRegex(ValueError, "reporting date"):
            run_agent(
                *args, thread_id=thread, question="Again", as_of_date="2026-11-05"
            )

    def test_timeout_retry_preserves_question_and_completed_tools(self):
        import httpx
        from openai import APITimeoutError

        from project_status_agent.agent.runner import saved_messages

        class TimeoutModel(ScriptedModel):
            def invoke(self, messages):
                self.requests.append(messages)
                if len(self.requests) == 1:
                    return call("detect_blockers", {"as_of_date": AS_OF})
                raise APITimeoutError(
                    request=httpx.Request("POST", "https://example.com")
                )

        args = (self.path, self.checkpoints, "PRJ-001", "csv")
        failing = TimeoutModel([])
        result = run_agent(
            *args,
            question="What is blocked?",
            as_of_date=AS_OF,
            interactive=True,
            model=failing,
            tools=self.tools,
        )
        self.assertEqual(result["status"], "needs_human")
        self.assertTrue(result["retryable"])
        self.assertIn("60 seconds", result["answer"])
        self.assertEqual(len(failing.requests), 2)
        thread = result["thread_id"]
        self.assertTrue(
            run_agent(*args, thread_id=thread, inspect_only=True)["retryable"]
        )
        recovered = ScriptedModel(
            [AIMessage(content="T-004 is blocked based on saved evidence.")]
        )
        result = run_agent(
            *args,
            thread_id=thread,
            retry=True,
            interactive=True,
            model=recovered,
            tools=self.tools,
        )
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["retryable"])
        self.assertEqual(result["tool_calls"], 1)
        self.assertEqual(len(result["tool_trace"]), 1)
        self.assertTrue(any(m.type == "tool" for m in recovered.requests[0]))
        messages = saved_messages(self.checkpoints, thread)
        self.assertEqual(sum(m.type == "human" for m in messages), 1)
        with self.assertRaisesRegex(ValueError, "Only a timed-out"):
            run_agent(*args, thread_id=thread, retry=True, model=recovered)

    def test_followup_reuses_scoped_tool_evidence_without_new_call(self):
        args = (self.path, self.checkpoints, "PRJ-001", "csv")
        first = run_agent(
            *args,
            question="Which tasks have persistent blockers?",
            as_of_date=AS_OF,
            model=ScriptedModel(
                [
                    call("get_persistent_blockers", {"as_of_date": AS_OF}),
                    AIMessage(content="T-004 and T-008 persist."),
                ]
            ),
            tools=self.tools,
        )
        followup = ScriptedModel(
            [AIMessage(content="Nina owns T-008; it depends on T-004.")]
        )
        result = run_agent(
            *args,
            thread_id=first["thread_id"],
            question="Who owns T-008?",
            model=followup,
            tools=self.tools,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(result["tool_calls"], 0)
        self.assertEqual(result["reused_evidence_count"], 1)
        self.assertEqual(result["tool_trace"], [])
        self.assertTrue(any(m.type == "tool" for m in followup.requests[0]))
        self.assertEqual(
            run_agent(*args, thread_id=first["thread_id"], inspect_only=True)["answer"],
            result["answer"],
        )
        for other_scope in [
            (self.path, self.checkpoints, "OTHER", "csv"),
            (self.path, self.checkpoints, "PRJ-001", "notion"),
        ]:
            with self.assertRaisesRegex(ValueError, "different project/source"):
                run_agent(
                    *other_scope,
                    thread_id=first["thread_id"],
                    question="Who owns it?",
                    model=followup,
                )
        with self.assertRaisesRegex(ValueError, "reporting date"):
            run_agent(
                *args,
                thread_id=first["thread_id"],
                question="And tomorrow?",
                as_of_date="2026-11-05",
                model=followup,
            )

    def test_followup_cannot_treat_previous_answers_as_evidence(self):
        args = (self.path, self.checkpoints, "PRJ-001", "csv")
        first = run_agent(
            *args,
            question="What is blocked?",
            as_of_date=AS_OF,
            model=ScriptedModel(
                [
                    AIMessage(content="A guessed answer"),
                    AIMessage(content="A guessed answer again"),
                ]
            ),
            tools=self.tools,
        )
        result = run_agent(
            *args,
            thread_id=first["thread_id"],
            question="Who owns it?",
            model=ScriptedModel(
                [
                    AIMessage(content="Another guessed answer"),
                    AIMessage(content="Still no evidence"),
                ]
            ),
            tools=self.tools,
        )
        self.assertEqual(first["status"], "needs_human")
        self.assertEqual(result["status"], "needs_human")
        self.assertEqual(result["reused_evidence_count"], 0)

    def test_reusable_evidence_rejects_errors_wrong_scope_and_cutoff(self):
        import json

        good = {
            "project_id": "PRJ-001",
            "source": "csv",
            "as_of_date": AS_OF,
            "results": {"count": 1},
        }
        valid = ToolMessage(
            content=json.dumps(good), name="detect_blockers", tool_call_id="ok"
        )
        self.assertEqual(count_reusable_evidence([valid], "PRJ-001", "csv", AS_OF), 1)
        invalid = [
            HumanMessage(content=json.dumps(good)),
            AIMessage(content=json.dumps(good)),
            ToolMessage(
                content=json.dumps(good),
                name="detect_blockers",
                tool_call_id="error",
                status="error",
            ),
            ToolMessage(content="not JSON", name="detect_blockers", tool_call_id="bad"),
            ToolMessage(
                content=json.dumps(good), name="sync_project_data", tool_call_id="sync"
            ),
        ]
        for key, value in [
            ("project_id", "OTHER"),
            ("source", "notion"),
            ("as_of_date", "2026-11-05"),
            ("as_of_date", "2026-11-02"),
            ("results", "invalid"),
        ]:
            invalid.append(
                ToolMessage(
                    content=json.dumps({**good, key: value}),
                    name="detect_blockers",
                    tool_call_id=key,
                )
            )
        self.assertEqual(count_reusable_evidence(invalid, "PRJ-001", "csv", AS_OF), 0)

    def test_missing_tool_selection_recovers_using_relevant_evidence(self):
        model = ScriptedModel(
            [
                AIMessage(content="An unsupported sprint summary"),
                call("fetch_sprint_data", {"as_of_date": AS_OF}),
                AIMessage(content="Sprint 3 has 12 tasks, including 3 blocked."),
            ]
        )
        result = self.execute(model)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["model_calls"], 3)
        self.assertEqual(result["tool_calls"], 1)
        self.assertEqual(result["evidence_retries"], 1)
        self.assertEqual(result["tool_errors"], 0)
        self.assertEqual(result["trace"][0]["event"], "evidence_required")
        self.assertTrue(
            any("registered read tool" in m.content for m in model.requests[1])
        )
        self.assertFalse(
            any(
                m.content == "An unsupported sprint summary" for m in result["messages"]
            )
        )

    def test_evidence_recovery_respects_model_budget(self):
        model = ScriptedModel([AIMessage(content="No tools selected")])
        result = self.execute(model, max_model_calls=1)
        self.assertEqual(result["status"], "needs_human")
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(result["evidence_retries"], 0)

    def test_evidence_recovery_does_not_reset_after_failed_tool(self):
        model = ScriptedModel(
            [
                AIMessage(content="No evidence yet"),
                call("unknown", {}),
                AIMessage(content="Still no evidence"),
            ]
        )
        result = self.execute(model)
        self.assertEqual(result["status"], "needs_human")
        self.assertEqual(len(model.requests), 3)
        self.assertEqual(result["evidence_retries"], 1)
        self.assertEqual(result["tool_errors"], 1)

    def test_agent_receives_shared_data_contract_before_tool_selection(self):
        from project_status_agent.data.contract import contract_instructions

        model = ScriptedModel(
            [
                call("detect_blockers", {"as_of_date": AS_OF}),
                AIMessage(content="The recorded blockers require review."),
            ]
        )
        result = self.execute(model)
        self.assertEqual(result["status"], "completed")
        self.assertIn(contract_instructions(), model.requests[0][0].content)
        self.assertIn("Project: PRJ-001. Source: csv.", model.requests[0][0].content)
        self.assertIn("query_sprint_history", model.requests[0][0].content)

    def test_instructions_separate_reporting_cutoff_from_actual_today(self):
        from unittest.mock import patch

        model = ScriptedModel(
            [
                call("detect_blockers", {"as_of_date": AS_OF}),
                AIMessage(content="Blockers are reported at the selected cutoff."),
            ]
        )
        with patch("project_status_agent.agent.workflow.datetime") as clock:
            clock.now.return_value = datetime(
                2026, 11, 20, tzinfo=ZoneInfo("America/Los_Angeles")
            )
            result = self.execute(model)
        self.assertEqual(result["status"], "completed")
        prompt = model.requests[0][0].content
        self.assertIn("Reporting cutoff: 2026-11-04", prompt)
        self.assertIn("Actual current date in America/Los_Angeles: 2026-11-20", prompt)
        self.assertIn("not the actual current date", prompt)
        self.assertIn("query_sprint_history", prompt)
        self.assertIn("acknowledge that gap explicitly", prompt)


if __name__ == "__main__":
    unittest.main()
