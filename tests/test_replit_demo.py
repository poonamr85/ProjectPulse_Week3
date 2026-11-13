"""Verify demo seeding and local-reference isolation without remote calls."""

import shutil
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from project_status_agent.config import PROJECT_ROOT
from project_status_agent.data.store import Store
from scripts.run_replit_demo import prepare_demo


class ReplitDemoTests(unittest.TestCase):
    def test_seed_is_isolated_and_restart_preserves_demo_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(
                PROJECT_ROOT / "sample_project_data_nov10",
                root / "sample_project_data_nov10",
            )
            reference = root / "data" / "project_status.sqlite3"
            reference.parent.mkdir()
            reference.write_bytes(b"local reference must not be touched")
            config = prepare_demo(root)
            checkpoint = Path(config["AGENT_CHECKPOINT_PATH"])
            checkpoint.write_bytes(b"existing demo checkpoints")
            with closing(Store(config["DATABASE_PATH"])) as store:
                self.assertEqual(len(store.records("PRJ-001", "csv", "tasks")), 20)
                self.assertEqual(len(store.records("PRJ-001", "csv", "history")), 200)
                self.assertEqual(store.records("PRJ-001", "notion", "tasks"), [])
                self.assertEqual(
                    store.db.execute("SELECT count(*) FROM sync_runs").fetchone()[0], 1
                )
            prepare_demo(root)
            with closing(Store(config["DATABASE_PATH"])) as store:
                self.assertEqual(
                    store.db.execute("SELECT count(*) FROM sync_runs").fetchone()[0], 1
                )
            self.assertEqual(reference.read_bytes(), b"local reference must not be touched")
            self.assertEqual(checkpoint.read_bytes(), b"existing demo checkpoints")
            self.assertEqual(config["PROJECT_DEFAULT_SOURCE"], "csv")
