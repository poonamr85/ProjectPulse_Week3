"""Shared entry-point settings preserve defaults and explicit workspace paths."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from project_status_agent.config import Settings


class SettingsTests(unittest.TestCase):
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
