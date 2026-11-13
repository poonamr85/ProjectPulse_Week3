"""Model setup tests use fake responses and never spend API credits."""

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from langchain_core.messages import AIMessage, ToolMessage
from openai import APIConnectionError, APITimeoutError

from project_status_agent.data.store import Store
from project_status_agent.data.validation import read_csv_bundle
from project_status_agent.integrations.http import ServiceError
from project_status_agent.integrations.model import (
    check_model,
    create_model,
    describe_model_error,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeModel:
    model_name = "fake-nebius-model"

    def __init__(self, call_date="2026-11-04", tool_name="fetch_sprint_data"):
        self.messages = []
        self.call_date = call_date
        self.tool_name = tool_name

    def bind_tools(self, tools, tool_choice):
        self.bound_tools = tools
        self.tool_choice = tool_choice
        return self

    def invoke(self, messages):
        self.messages.append(messages)
        if len(self.messages) == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": self.tool_name,
                        "args": {"as_of_date": self.call_date},
                        "id": "call-1",
                    }
                ],
            )
        return AIMessage(
            content="Sprint 3 has 12 tasks: 3 blocked, 5 in progress, 2 done, 2 to do."
        )


class ModelTests(unittest.TestCase):
    def test_transport_failures_have_distinct_safe_messages(self):
        request = httpx.Request("POST", "https://example.com/v1/chat/completions")
        timeout = describe_model_error(APITimeoutError(request=request))
        connection = describe_model_error(
            APIConnectionError(request=request, message="private-debug-value")
        )
        self.assertIn("timeout", timeout)
        self.assertIn("Could not connect", connection)
        self.assertNotIn("private-debug-value", connection)
        self.assertNotIn("HTTP unavailable", timeout + connection)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "project.sqlite3")
        store = Store(self.path)
        try:
            bundle = read_csv_bundle(ROOT / "sample_project_data")
            store.sync("PRJ-001", "csv", lambda: copy.deepcopy(bundle))
        finally:
            store.close()

    def tearDown(self):
        self.directory.cleanup()

    def test_two_turn_tool_roundtrip(self):
        model = FakeModel()
        result = check_model(self.path, "PRJ-001", "csv", "2026-11-04", model)
        self.assertEqual(result["tool_result"]["task_count"], 12)
        self.assertEqual(len(model.messages), 2)
        self.assertEqual(model.tool_choice, "auto")
        self.assertEqual(
            [tool.name for tool in model.bound_tools], ["fetch_sprint_data"]
        )
        self.assertIsInstance(model.messages[-1][-1], ToolMessage)
        self.assertNotIn('"tasks":', model.messages[-1][-1].content)

    def test_unexpected_date_and_tool_are_rejected(self):
        for model in (
            FakeModel(call_date="2026-11-05"),
            FakeModel(tool_name="sync_project_data"),
        ):
            with self.assertRaisesRegex(
                ServiceError, "unexpected tool or reporting date"
            ):
                check_model(self.path, "PRJ-001", "csv", "2026-11-04", model)
            self.assertEqual(len(model.messages), 1)

    def test_no_tool_call_is_not_claimed_success(self):
        model = FakeModel()
        model.invoke = lambda messages: AIMessage(content="No tool requested")
        with self.assertRaisesRegex(ServiceError, "valid tool call"):
            check_model(self.path, "PRJ-001", "csv", "2026-11-04", model)

    def test_configuration_targets_nebius_not_openai(self):
        settings = {
            "NEBIUS_API_KEY": "fake-private-key",
            "NEBIUS_MODEL": "zai-org/GLM-5.2",
            "NEBIUS_BASE_URL": "https://api.tokenfactory.nebius.com/v1",
            "NEBIUS_TIMEOUT_SECONDS": "120",
        }
        with (
            patch.dict(os.environ, settings),
            patch("project_status_agent.integrations.model.ChatOpenAI") as constructor,
        ):
            create_model()
            kwargs = constructor.call_args.kwargs
            self.assertEqual(kwargs["model"], settings["NEBIUS_MODEL"])
            self.assertEqual(kwargs["base_url"], settings["NEBIUS_BASE_URL"])
            self.assertFalse(kwargs["use_responses_api"])
            self.assertEqual(kwargs["max_retries"], 1)
            self.assertEqual(kwargs["timeout"], 120)

    def test_timeout_can_be_configured_and_invalid_values_are_rejected(self):
        settings = {
            "NEBIUS_API_KEY": "fake",
            "NEBIUS_MODEL": "test",
            "NEBIUS_BASE_URL": "https://api.tokenfactory.nebius.com/v1",
        }
        with (
            patch.dict(os.environ, {**settings, "NEBIUS_TIMEOUT_SECONDS": "90"}),
            patch("project_status_agent.integrations.model.ChatOpenAI") as constructor,
        ):
            create_model()
            self.assertEqual(constructor.call_args.kwargs["timeout"], 90)
        for value in ["0", "999", "nan", "inf", "invalid"]:
            with (
                patch.dict(os.environ, {**settings, "NEBIUS_TIMEOUT_SECONDS": value}),
                self.assertRaisesRegex(ValueError, "NEBIUS_TIMEOUT_SECONDS"),
            ):
                create_model()

    def test_qa_timeout_and_retries_are_separate_from_reports(self):
        settings = {
            "NEBIUS_API_KEY": "fake",
            "NEBIUS_MODEL": "test",
            "NEBIUS_BASE_URL": "https://example.com/v1",
            "NEBIUS_TIMEOUT_SECONDS": "120",
            "NEBIUS_QA_TIMEOUT_SECONDS": "60",
        }
        with (
            patch.dict(os.environ, settings),
            patch("project_status_agent.integrations.model.ChatOpenAI") as constructor,
        ):
            create_model(interactive=True)
            self.assertEqual(constructor.call_args.kwargs["timeout"], 60)
            self.assertEqual(constructor.call_args.kwargs["max_retries"], 0)
            create_model()
            self.assertEqual(constructor.call_args.kwargs["timeout"], 120)
            self.assertEqual(constructor.call_args.kwargs["max_retries"], 1)
        with (
            patch.dict(
                os.environ, {**settings, "NEBIUS_QA_TIMEOUT_SECONDS": "invalid"}
            ),
            self.assertRaisesRegex(ValueError, "NEBIUS_QA_TIMEOUT_SECONDS"),
        ):
            create_model(interactive=True)


if __name__ == "__main__":
    unittest.main()
