"""Unit 12: builder guide and prompt builder."""

from pathlib import Path

import pytest

from orchestrator.config import load_factory_config
from orchestrator.errors import ConfigError, PromptError
from orchestrator.models.enums import AgentRole
from orchestrator.models.envelopes import Finding, VerifyOutput
from orchestrator.models.settings import Settings
from orchestrator.prompt_builder import (
    Guide,
    build_builder_prompt,
    default_prompts_dir,
    forbid_secrets,
    load_guide,
)

ROOT = Path(__file__).resolve().parents[2]
FACTORY_CONFIG = ROOT / "fixtures" / "factory_config"
SPEC = """# 418 — Invoice due date

## Outcome
Invoices expose a due date.

## Scope
src/billing/invoice.py

## Non-goals
Caching of invoice queries.

## Acceptance criteria
1. WHEN an invoice is read THE SYSTEM SHALL return due_date.

## Constraints
No ADR applies.

## Open questions
None.

## Test plan
AC1 -> tests/unit/test_invoice_due_date.py::test_due_date_adds_terms
"""


def test_load_guide_factory_defaults_and_sha() -> None:
    guide = load_guide(AgentRole.BUILDER)
    assert guide.source == {"system.md": "factory", "user.md": "factory"}
    assert len(guide.sha) == 64
    assert guide.sha == load_guide(AgentRole.BUILDER).sha
    system_file = default_prompts_dir() / "builder" / "system.md"
    assert guide.system_template == system_file.read_text(encoding="utf-8")
    assert "{{" not in guide.system_template
    for role in (AgentRole.PLANNER, AgentRole.REVIEWER):
        assert load_guide(role).source == {"system.md": "factory", "user.md": "factory"}
    with pytest.raises(ConfigError, match="prompt guide missing"):
        load_guide(AgentRole.REVIEWER, prompts_dir=Path("does-not-exist"))


def test_override_replaces_only_present_files(tmp_path: Path) -> None:
    override = tmp_path / "prompts" / "builder"
    override.mkdir(parents=True)
    (override / "system.md").write_text("# Builder (override)\n", encoding="utf-8")
    factory = load_guide(AgentRole.BUILDER)
    guide = load_guide(AgentRole.BUILDER, override_dir=override)
    assert guide.source == {"system.md": "override", "user.md": "factory"}
    assert guide.system_template == "# Builder (override)\n"
    assert guide.user_template == factory.user_template
    assert guide.sha != factory.sha


def test_sections_in_order_and_system_prompt_untouched() -> None:
    guide = load_guide(AgentRole.BUILDER)
    bundle = build_builder_prompt(
        guide,
        spec_text=SPEC,
        writes=("**",),
        protected_paths=(".factory/", "tests/acceptance/", "uv.lock"),
        commands=load_factory_config(FACTORY_CONFIG).quality,
        focused_tests=["tests/unit/test_invoice_due_date.py::test_due_date_adds_terms"],
    )
    assert bundle.system_prompt == guide.system_template
    assert bundle.guide_sha == guide.sha
    assert bundle.sections == ("write_boundary", "command_names", "spec")
    text = bundle.user_prompt
    order = [text.index(h) for h in ("## Write boundary", "## Commands", "## Spec")]
    assert order == sorted(order)
    assert "You may write under: **." in text
    assert "tests/acceptance/" in text and "uv.lock" in text
    assert (
        "test: uv run pytest -q tests/unit/test_invoice_due_date.py::test_due_date_adds_terms"
        in text
    )
    assert "WHEN an invoice is read THE SYSTEM SHALL return due_date." in text
    assert "Previous verification result" not in text
    assert "Reviewer findings" not in text
    assert "{{" not in text


def test_repair_and_revision_sections() -> None:
    guide = load_guide(AgentRole.BUILDER)
    cfg = load_factory_config(FACTORY_CONFIG)
    verify = VerifyOutput(
        status="fail",
        gate="typecheck",
        violations=["src/billing/invoice.py:88: incompatible type"],
        command="uv run mypy .",
        exit_code=1,
        output_tail="error: Argument 1 has incompatible type",
    )
    repair = build_builder_prompt(
        guide,
        spec_text=SPEC,
        writes=cfg.roster.builder.writes,
        protected_paths=cfg.protected_paths,
        commands=cfg.quality,
        previous_verify=verify,
    )
    assert repair.sections[-1] == "previous_verify"
    assert "## Previous verification result" in repair.user_prompt
    assert '"gate": "typecheck"' in repair.user_prompt
    assert repair.user_prompt.index("## Spec") < repair.user_prompt.index(
        "## Previous verification"
    )

    finding = Finding(
        requirement_id="AC 1",
        met=False,
        severity="Major",
        file="src/api/invoices.py",
        line=41,
        problem="due_date omitted from the list response",
    )
    revision = build_builder_prompt(
        guide,
        spec_text=SPEC,
        writes=cfg.roster.builder.writes,
        protected_paths=cfg.protected_paths,
        commands=cfg.quality,
        reviewer_findings=[finding],
    )
    assert revision.sections == ("write_boundary", "command_names", "spec", "reviewer_findings")
    assert "## Reviewer findings" in revision.user_prompt
    assert '"requirement_id": "AC 1"' in revision.user_prompt


def test_prompt_is_deterministic() -> None:
    guide = load_guide(AgentRole.BUILDER)
    cfg = load_factory_config(FACTORY_CONFIG)
    kwargs = {
        "spec_text": SPEC,
        "writes": cfg.roster.builder.writes,
        "protected_paths": cfg.protected_paths,
        "commands": cfg.quality,
    }
    first = build_builder_prompt(guide, **kwargs)  # type: ignore[arg-type]
    second = build_builder_prompt(guide, **kwargs)  # type: ignore[arg-type]
    assert first == second
    # the stable prefix for prompt caching: everything before the optional tail
    prefix = first.system_prompt + first.user_prompt
    assert prefix == second.system_prompt + second.user_prompt


@pytest.mark.parametrize(
    "secret",
    [
        "token ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "key sk-ant-api03-abcdefghij",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        "ANTHROPIC_API_KEY=supersecretvalue",
        "github_pat_11ABCDEFG0123456789abcdefghijk",
    ],
)
def test_secret_shapes_are_refused(secret: str) -> None:
    guide = load_guide(AgentRole.BUILDER)
    cfg = load_factory_config(FACTORY_CONFIG)
    with pytest.raises(PromptError, match="credential-shaped"):
        build_builder_prompt(
            guide,
            spec_text=SPEC + "\n" + secret,
            writes=cfg.roster.builder.writes,
            protected_paths=cfg.protected_paths,
            commands=cfg.quality,
        )


def test_non_text_inputs_are_refused() -> None:
    guide = load_guide(AgentRole.BUILDER)
    cfg = load_factory_config(FACTORY_CONFIG)
    for bad in (Settings(), {"token": "x"}, 42):
        with pytest.raises(PromptError, match="must be text"):
            build_builder_prompt(
                guide,
                spec_text=bad,  # type: ignore[arg-type]
                writes=cfg.roster.builder.writes,
                protected_paths=cfg.protected_paths,
                commands=cfg.quality,
            )
    with pytest.raises(PromptError, match="must be text"):
        forbid_secrets("fine", None)
    with pytest.raises(PromptError, match="builder guide"):
        build_builder_prompt(
            Guide(AgentRole.PLANNER, "s", "u", {}),
            spec_text=SPEC,
            writes=("specs/",),
            protected_paths=(),
            commands=cfg.quality,
        )


def test_unfilled_placeholder_is_an_error() -> None:
    cfg = load_factory_config(FACTORY_CONFIG)
    guide = Guide(AgentRole.BUILDER, "system", "{{spec}} {{mystery}}", {})
    with pytest.raises(PromptError, match="mystery"):
        build_builder_prompt(
            guide,
            spec_text=SPEC,
            writes=("**",),
            protected_paths=(),
            commands=cfg.quality,
        )
