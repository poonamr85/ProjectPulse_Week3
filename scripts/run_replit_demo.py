"""Seed an isolated synthetic workspace and launch the temporary Replit demo."""

import os
import sys
from contextlib import closing
from pathlib import Path

from project_status_agent.config import PROJECT_ROOT
from project_status_agent.data.store import Store
from project_status_agent.data.validation import read_csv_bundle


def prepare_demo(root: Path = PROJECT_ROOT) -> dict[str, str]:
    """Initialize CSV facts once; preserve existing demo reports and checkpoints."""
    folder = root / "data" / "replit_demo"
    database = folder / "project_status.sqlite3"
    checkpoints = folder / "agent_checkpoints.sqlite3"
    with closing(Store(str(database))) as store:
        if not store.records("PRJ-001", "csv", "history"):
            store.sync(
                "PRJ-001",
                "csv",
                lambda: read_csv_bundle(root / "sample_project_data_nov10"),
            )
    return {
        "DATABASE_PATH": str(database),
        "AGENT_CHECKPOINT_PATH": str(checkpoints),
        "PROJECT_ID": "PRJ-001",
        "PROJECT_DEFAULT_SOURCE": "csv",
    }


def main() -> None:
    os.chdir(PROJECT_ROOT)
    # Dedicated paths override other settings only in this demo process.
    os.environ.update(prepare_demo())
    print("Synthetic CSV demo ready through November 10, 2026.", flush=True)
    print("Temporary SQLite state: data/replit_demo/", flush=True)
    os.execv(
        sys.executable,
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "app.py",
            "--server.address=0.0.0.0",
            "--server.port=8501",
            "--server.headless=true",
        ],
    )


if __name__ == "__main__":
    main()
