"""The OpenAPI 3.1 document of ``/api/v1``, committed as ``openapi.json`` (TRD §6.1, §10).

The document is generated from the pydantic models in ``models/api.py``;
``factory openapi --check`` fails when the committed file no longer
matches the application, so a contract change is always a reviewed diff.
"""

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI

from orchestrator.api.app import ApiDeps, create_app
from orchestrator.intake.webhook import GitHubIpRanges
from orchestrator.models.settings import Settings
from orchestrator.trace import TraceDB, TraceWriter

DOCUMENT = Path(__file__).resolve().parents[2] / "openapi.json"


def document(app: FastAPI) -> dict[str, object]:
    return dict(app.openapi())


def render(app: FastAPI) -> str:
    return json.dumps(document(app), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def documented_app() -> FastAPI:
    """An application over an empty trace, enough to describe every route."""
    scratch = Path(tempfile.mkdtemp(prefix="factory-openapi-"))
    db = TraceDB.open(scratch / "trace.db")
    deps = ApiDeps(
        writer=TraceWriter(db),
        settings=Settings(data_dir=scratch),
        ip_ranges=GitHubIpRanges((), datetime.now(UTC)),
    )
    return create_app(deps)


def write_document(path: Path = DOCUMENT) -> Path:
    path.write_text(render(documented_app()), encoding="utf-8", newline="\n")
    return path


def check_document(path: Path = DOCUMENT) -> bool:
    """True when the committed document matches the application."""
    if not path.is_file():
        return False
    return path.read_text(encoding="utf-8") == render(documented_app())


__all__ = ["DOCUMENT", "check_document", "document", "documented_app", "render", "write_document"]
