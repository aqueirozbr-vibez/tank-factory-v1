"""Read `.factory/` from disk into :class:`FactoryConfig`.

In phases 1 and 2 the directory is a local checkout; from phase 3 the
same loader runs over the tree fetched from `main` on every `push`.
"""

import hashlib
from pathlib import Path

import yaml
from pydantic import ValidationError

from orchestrator import constants
from orchestrator.errors import ConfigError
from orchestrator.models.config import (
    FactoryConfig,
    FactorySettings,
    QualityCommands,
    Roster,
    validation_error,
)

REQUIRED_FILES = ("roster.yaml", "quality.yaml", "risk.yaml", "review-policy.yaml")
OPTIONAL_SETTINGS = "factory.yaml"


def tree_sha(root: Path) -> str:
    """SHA-256 over every file under ``root``: ``path NUL size NUL bytes``, sorted by path."""
    digest = hashlib.sha256()
    for file in sorted(p for p in root.rglob("*") if p.is_file()):
        data = file.read_bytes()
        rel = file.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
    return digest.hexdigest()


def _read_text(root: Path, name: str) -> str:
    path = root / name
    if not path.is_file():
        raise ConfigError(f"{name} is missing from {root}", detail={"file": name})
    return path.read_text(encoding="utf-8")


def _read_yaml(root: Path, name: str) -> dict[str, object]:
    text = _read_text(root, name)
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{name} is not valid YAML: {exc}", detail={"file": name}) from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"{name} must be a mapping at the top level", detail={"file": name})
    return {str(k): v for k, v in loaded.items()}


def load_factory_config(root: Path, *, main_commit: str = "local") -> FactoryConfig:
    if not root.is_dir():
        raise ConfigError(f".factory directory not found: {root}")
    roster_raw = _read_yaml(root, "roster.yaml")
    quality_raw = _read_yaml(root, "quality.yaml")
    settings_raw = (
        _read_yaml(root, OPTIONAL_SETTINGS) if (root / OPTIONAL_SETTINGS).is_file() else {}
    )
    risk_yaml = _read_text(root, "risk.yaml")
    review_policy_yaml = _read_text(root, "review-policy.yaml")

    try:
        roster = Roster.model_validate(roster_raw)
    except ValidationError as exc:
        raise validation_error(exc, "roster.yaml") from exc
    try:
        quality = QualityCommands.model_validate(quality_raw)
    except ValidationError as exc:
        raise validation_error(exc, "quality.yaml") from exc
    try:
        settings = FactorySettings.model_validate(settings_raw)
    except ValidationError as exc:
        raise validation_error(exc, OPTIONAL_SETTINGS) from exc

    protected = sorted(
        set(constants.PROTECTED_PATHS_DEFAULT)
        | set(constants.LOCKFILES)
        | set(settings.protected_paths)
    )
    return FactoryConfig(
        root=root,
        config_sha=tree_sha(root),
        main_commit=main_commit,
        roster=roster,
        quality=quality,
        settings=settings,
        protected_paths=tuple(protected),
        risk_yaml=risk_yaml,
        review_policy_yaml=review_policy_yaml,
    )
