"""Shared CLI plumbing: settings, the trace database and output helpers."""

import sys
from pathlib import Path

from orchestrator.models.settings import Settings
from orchestrator.trace import TraceDB, TraceWriter


def settings_for(data_dir: Path | None) -> Settings:
    if data_dir is None:
        return Settings()
    return Settings(data_dir=data_dir.resolve())


def open_trace(settings: Settings) -> tuple[TraceDB, TraceWriter]:
    db = TraceDB.open(settings.trace_db_path)
    return db, TraceWriter(db)


def say(text: str) -> None:
    print(text)


def warn(text: str) -> None:
    print(text, file=sys.stderr)
