"""Load local configuration without printing secrets or requiring packages."""

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    """Shared non-secret workspace settings, read when an entry point starts."""

    database_path: str
    checkpoint_path: str
    project_id: str
    notion_version: str

    @classmethod
    def from_env(cls, root: Path | None = None) -> "Settings":
        database = os.environ.get("DATABASE_PATH", "data/project_status.sqlite3")
        checkpoints = os.environ.get(
            "AGENT_CHECKPOINT_PATH", "data/agent_checkpoints.sqlite3"
        )
        if root is not None:
            database = str((root / database).resolve())
            checkpoints = str((root / checkpoints).resolve())
        return cls(
            database,
            checkpoints,
            os.environ.get("PROJECT_ID", "PRJ-001"),
            os.environ.get("NOTION_VERSION", "2025-09-03"),
        )


def load_env(path: str = ".env") -> None:
    file = Path(path)
    if not file.exists():
        return
    for line in file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.replace("_", "").isalnum():
            raise ValueError("Invalid .env entry. Use KEY=value, one per line.")
        os.environ.setdefault(key, value.strip().strip("\"'"))


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Set {name} in your local .env file.")
    return value
