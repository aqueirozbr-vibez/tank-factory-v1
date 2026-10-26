"""``factory openapi``: write or check the committed OpenAPI document (TRD §6.1)."""

from pathlib import Path

from cli.common import say
from orchestrator.api.openapi import DOCUMENT, check_document, write_document


def openapi(*, out: Path | None = None, check: bool = False) -> int:
    path = out or DOCUMENT
    if check:
        if check_document(path):
            say(f"{path} matches the application")
            return 0
        say(f"{path} is stale: run `factory openapi` and commit the result")
        return 1
    write_document(path)
    say(f"wrote {path}")
    return 0


__all__ = ["openapi"]
