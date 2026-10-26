"""GitHub as the system of record, reached only through this package (TRD §2.1)."""

from orchestrator.github.auth import InstallationTokenMinter, TokenProvider, app_jwt
from orchestrator.github.client import EventRecorder, GitHubApiError, GitHubClient
from orchestrator.github.codeowners import (
    is_owner,
    load_codeowners,
    owners_for,
    parse_codeowners,
)

__all__ = [
    "EventRecorder",
    "GitHubApiError",
    "GitHubClient",
    "InstallationTokenMinter",
    "TokenProvider",
    "app_jwt",
    "is_owner",
    "load_codeowners",
    "owners_for",
    "parse_codeowners",
]
