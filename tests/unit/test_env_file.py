"""Operator ergonomics: the gitignored .env file the CLI loads."""

from pathlib import Path

import pytest

from orchestrator.models.settings import load_env_file


def test_load_env_file_sets_missing_keys_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n\nANTHROPIC_API_KEY='sk-ant-FAKE-example'\nexport FACTORY_CLAUDE_BARE=0\n"
        'ALREADY="from-file"\nbroken line without equals\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("FACTORY_CLAUDE_BARE", raising=False)
    monkeypatch.setenv("ALREADY", "from-shell")
    loaded = load_env_file(env)
    assert loaded == ["ANTHROPIC_API_KEY", "FACTORY_CLAUDE_BARE"]
    import os

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-FAKE-example"
    assert os.environ["FACTORY_CLAUDE_BARE"] == "0"
    assert os.environ["ALREADY"] == "from-shell"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.delenv("FACTORY_CLAUDE_BARE")


def test_missing_env_file_is_fine(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / "nope.env") == []
