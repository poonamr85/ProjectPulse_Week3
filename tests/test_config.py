"""Shared entry-point settings preserve defaults and explicit workspace paths."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from project_status_agent.config import Settings, configure_tracing, load_env


class SettingsTests(unittest.TestCase):
    def test_agent_addressing_removes_all_legacy_destinations(self):
        values = {
            "LANGSMITH_AGENT_ID": "intelligent-project-status",
            "LANGSMITH_AGENT_ENVIRONMENT": "development",
            "LANGSMITH_ENDPOINT": "https://api.smith.langchain.com",
            "LANGSMITH_PROJECT": "old-project",
            "LANGCHAIN_PROJECT": "other-project",
            "LANGCHAIN_SESSION": "legacy-session",
        }
        with patch.dict(os.environ, values, clear=True):
            # Replit supplies secrets without a local .env file.
            load_env("/missing-project-pulse-test.env")
            self.assertFalse(
                {"LANGSMITH_PROJECT", "LANGCHAIN_PROJECT", "LANGCHAIN_SESSION"}
                & os.environ.keys()
            )
            self.assertEqual(os.environ["LANGSMITH_AGENT_ENVIRONMENT"], "development")
            self.assertEqual(
                os.environ["LANGSMITH_ENDPOINT"], "https://api.smith.langchain.com"
            )

    def test_project_only_configuration_remains_available(self):
        with patch.dict(os.environ, {"LANGSMITH_PROJECT": "legacy"}, clear=True):
            configure_tracing()
            self.assertEqual(os.environ["LANGSMITH_PROJECT"], "legacy")

    def test_default_paths_remain_relative_for_terminal_commands(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.database_path, "data/project_status.sqlite3")
        self.assertEqual(settings.checkpoint_path, "data/agent_checkpoints.sqlite3")
        self.assertEqual(settings.project_id, "PRJ-001")

    def test_ui_resolves_relative_paths_and_keeps_absolute_overrides(self):
        values = {
            "DATABASE_PATH": "custom/records.sqlite3",
            "AGENT_CHECKPOINT_PATH": "/tmp/custom-checkpoints.sqlite3",
            "PROJECT_ID": "PRJ-002",
            "NOTION_VERSION": "2025-09-03",
        }
        with patch.dict(os.environ, values, clear=True):
            settings = Settings.from_env(Path("/tmp/project-pulse"))
        self.assertEqual(
            settings.database_path,
            str(Path("/tmp/project-pulse/custom/records.sqlite3").resolve()),
        )
        self.assertEqual(
            settings.checkpoint_path,
            str(Path("/tmp/custom-checkpoints.sqlite3").resolve()),
        )
        self.assertEqual(settings.project_id, "PRJ-002")
