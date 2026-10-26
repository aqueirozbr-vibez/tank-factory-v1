"""The only place a prompt is assembled (architecture.md · Invariant 13).

Each role has an allow-list of named inputs. Nothing from ``os.environ``, a
settings object, a header or the issue thread can reach a builder prompt,
because this module only accepts the fields below and scans each one.
"""

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from orchestrator import constants
from orchestrator.errors import ConfigError, PromptError
from orchestrator.models.config import QualityCommands
from orchestrator.models.enums import AgentRole
from orchestrator.models.envelopes import Finding, VerifyOutput

_PACKAGED_PROMPTS = Path(__file__).resolve().parent / "prompts"
_REPO_PROMPTS = Path(__file__).resolve().parents[1] / "prompts"

_SECRET_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{16,}"),
    re.compile(r"\b[A-Z0-9_]*(API_KEY|SECRET|TOKEN|PASSWORD)\s*=\s*\S{8,}"),
)

BUILDER_SECTIONS = (
    "write_boundary",
    "command_names",
    "spec",
    "previous_verify",
    "reviewer_findings",
)


def default_prompts_dir() -> Path:
    return _REPO_PROMPTS if _REPO_PROMPTS.is_dir() else _PACKAGED_PROMPTS


@dataclass(frozen=True)
class Guide:
    role: AgentRole
    system_template: str
    user_template: str
    source: dict[str, str]  # file name → "factory" | "override"

    @property
    def sha(self) -> str:
        digest = hashlib.sha256()
        digest.update(self.system_template.encode("utf-8"))
        digest.update(b"\0")
        digest.update(self.user_template.encode("utf-8"))
        return digest.hexdigest()


@dataclass(frozen=True)
class PromptBundle:
    role: AgentRole
    system_prompt: str
    user_prompt: str
    guide_sha: str
    sections: tuple[str, ...]


def forbid_secrets(*texts: object, where: str = "prompt input") -> None:
    """Refuse any input that is not text or that looks like a credential."""
    for text in texts:
        if not isinstance(text, str):
            raise PromptError(
                f"{where} must be text, got {type(text).__name__}; prompts take named fields only"
            )
        for pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                raise PromptError(f"{where} contains a credential-shaped value ({pattern.pattern})")


def load_guide(
    role: AgentRole, *, override_dir: Path | None = None, prompts_dir: Path | None = None
) -> Guide:
    base = (prompts_dir or default_prompts_dir()) / str(role)
    texts: dict[str, str] = {}
    source: dict[str, str] = {}
    for name in ("system.md", "user.md"):
        override = override_dir / name if override_dir is not None else None
        if override is not None and override.is_file():
            texts[name] = override.read_text(encoding="utf-8")
            source[name] = "override"
            continue
        default = base / name
        if not default.is_file():
            raise ConfigError(f"prompt guide missing: {default}", detail={"role": str(role)})
        texts[name] = default.read_text(encoding="utf-8")
        source[name] = "factory"
    return Guide(role, texts["system.md"], texts["user.md"], source)


def _render(template: str, values: dict[str, str]) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", value)
    leftover = re.search(r"\{\{\s*[a-z_]+\s*\}\}", out)
    if leftover:
        raise PromptError(f"template placeholder without a value: {leftover.group(0)}")
    return out


def render_write_boundary(
    writes: Sequence[str], protected_paths: Sequence[str], test_dirs: Sequence[str]
) -> str:
    lines = [
        "You may write under: " + ", ".join(writes) + ".",
        "You may not write under (rolled back, task ends): " + ", ".join(protected_paths) + ".",
        "Dependency manifests are off limits: " + ", ".join(constants.DEPENDENCY_MANIFESTS) + ".",
        "New tests only under: " + ", ".join(test_dirs) + ".",
    ]
    return "\n".join(lines)


def render_commands(commands: QualityCommands, focused_tests: Sequence[str]) -> str:
    test_line = " ".join([*commands.test, *focused_tests])
    return "\n".join(
        [
            f"format: {' '.join(commands.format)}",
            f"lint: {' '.join(commands.lint)}",
            f"typecheck: {' '.join(commands.typecheck)}",
            f"test: {test_line}",
        ]
    )


def build_builder_prompt(
    guide: Guide,
    *,
    spec_text: str,
    writes: Sequence[str],
    protected_paths: Sequence[str],
    commands: QualityCommands,
    focused_tests: Sequence[str] = (),
    test_dirs: Sequence[str] = constants.BUILDER_TEST_DIRS,
    previous_verify: VerifyOutput | None = None,
    reviewer_findings: Sequence[Finding] | None = None,
) -> PromptBundle:
    """Assemble the builder prompt from its allow-listed fields, in the PRD's order."""
    if guide.role is not AgentRole.BUILDER:
        raise PromptError(f"build_builder_prompt needs the builder guide, got {guide.role}")
    forbid_secrets(spec_text, where="spec")
    forbid_secrets(*writes, *protected_paths, *focused_tests, *test_dirs, where="path")
    sections = ["write_boundary", "command_names", "spec"]
    previous = ""
    if previous_verify is not None:
        if not isinstance(previous_verify, VerifyOutput):
            raise PromptError("previous_verify must be a VerifyOutput")
        previous = (
            "\n## Previous verification result\n\n"
            "Fix exactly these violations in this session.\n\n```json\n"
            + previous_verify.model_dump_json(indent=2)
            + "\n```\n"
        )
        sections.append("previous_verify")
    findings_text = ""
    if reviewer_findings:
        rendered = [f.model_dump(mode="json") for f in reviewer_findings]
        if not all(isinstance(f, Finding) for f in reviewer_findings):
            raise PromptError("reviewer_findings must be Finding objects")
        findings_text = (
            "\n## Reviewer findings\n\nAddress each finding or explain why it does not apply.\n\n"
            "```json\n" + json.dumps(rendered, indent=2, sort_keys=True) + "\n```\n"
        )
        sections.append("reviewer_findings")
    user_prompt = _render(
        guide.user_template,
        {
            "write_boundary": render_write_boundary(writes, protected_paths, test_dirs),
            "command_names": render_commands(commands, focused_tests),
            "spec": spec_text.strip() + "\n",
            "previous_verify": previous,
            "reviewer_findings": findings_text,
        },
    )
    forbid_secrets(user_prompt, guide.system_template, where="assembled prompt")
    return PromptBundle(
        role=AgentRole.BUILDER,
        system_prompt=guide.system_template,
        user_prompt=user_prompt,
        guide_sha=guide.sha,
        sections=tuple(sections),
    )


# --- planner ------------------------------------------------------------------------

PLANNER_SECTIONS = ("spec_template", "agents_md", "adr_index", "issue")


def load_prompt_text(
    role: AgentRole,
    name: str,
    *,
    override_dir: Path | None = None,
    prompts_dir: Path | None = None,
) -> str:
    """An extra guide file for a role (factory default or `.factory/prompts/` override)."""
    override = override_dir / name if override_dir is not None else None
    if override is not None and override.is_file():
        return override.read_text(encoding="utf-8")
    default = (prompts_dir or default_prompts_dir()) / str(role) / name
    if not default.is_file():
        raise ConfigError(f"prompt file missing: {default}", detail={"role": str(role)})
    return default.read_text(encoding="utf-8")


def build_planner_prompt(
    guide: Guide,
    *,
    spec_path: str,
    spec_template: str,
    issue_block: str,
    agents_md: str | None = None,
    adr_index: Sequence[str] = (),
    correction: str | None = None,
) -> PromptBundle:
    """Assemble the planner prompt in the TRD §8 order."""
    if guide.role is not AgentRole.PLANNER:
        raise PromptError(f"build_planner_prompt needs the planner guide, got {guide.role}")
    forbid_secrets(spec_path, where="spec path")
    forbid_secrets(spec_template, where="spec template")
    forbid_secrets(issue_block, where="issue")
    if agents_md is not None:
        forbid_secrets(agents_md, where="repository guidance")
    forbid_secrets(*adr_index, where="ADR index")
    sections = list(PLANNER_SECTIONS)
    tail = ""
    if correction:
        forbid_secrets(correction, where="correction")
        tail = (
            "\n## Correction\n\nFix exactly these violations in this session.\n\n"
            + correction
            + "\n"
        )
        sections.append("correction")
    user_prompt = _render(
        guide.user_template,
        {
            "spec_path": spec_path,
            "spec_template": spec_template.strip() + "\n",
            "agents_md": (agents_md.strip() + "\n")
            if agents_md
            else "No CLAUDE.md or AGENTS.md in this repository.\n",
            "adr_index": ("\n".join(f"- {entry}" for entry in adr_index) + "\n")
            if adr_index
            else "No ADRs under docs/adr/.\n",
            "issue": issue_block.strip() + "\n",
            "correction": tail,
        },
    )
    forbid_secrets(user_prompt, guide.system_template, where="assembled prompt")
    return PromptBundle(
        role=AgentRole.PLANNER,
        system_prompt=guide.system_template,
        user_prompt=user_prompt,
        guide_sha=guide.sha,
        sections=tuple(sections),
    )


# --- reviewer -----------------------------------------------------------------------

REVIEWER_SECTIONS = ("policy", "spec", "builder_envelope", "gate_evidence", "diff")


def build_reviewer_prompt(
    guide: Guide,
    *,
    policy_text: str,
    spec_text: str,
    builder_envelope_text: str,
    gate_evidence: str,
    diff: str,
    correction: str | None = None,
) -> PromptBundle:
    """Assemble the reviewer prompt in the architecture's order; the diff is scanned too."""
    if guide.role is not AgentRole.REVIEWER:
        raise PromptError(f"build_reviewer_prompt needs the reviewer guide, got {guide.role}")
    forbid_secrets(policy_text, where="review policy")
    forbid_secrets(spec_text, where="spec")
    forbid_secrets(builder_envelope_text, where="builder envelope")
    forbid_secrets(gate_evidence, where="gate evidence")
    forbid_secrets(diff, where="diff")
    sections = list(REVIEWER_SECTIONS)
    tail = ""
    if correction:
        forbid_secrets(correction, where="correction")
        tail = (
            "\n## Correction\n\nReturn the envelope again with exactly these problems fixed.\n\n"
            + correction
            + "\n"
        )
        sections.append("correction")
    user_prompt = _render(
        guide.user_template,
        {
            "policy": policy_text.strip() + "\n",
            "spec": spec_text.strip() + "\n",
            "builder_envelope": builder_envelope_text.strip() + "\n",
            "gate_evidence": gate_evidence.strip() + "\n",
            "diff": (diff.rstrip() + "\n") if diff.strip() else "(empty diff)\n",
            "correction": tail,
        },
    )
    forbid_secrets(user_prompt, guide.system_template, where="assembled prompt")
    return PromptBundle(
        role=AgentRole.REVIEWER,
        system_prompt=guide.system_template,
        user_prompt=user_prompt,
        guide_sha=guide.sha,
        sections=tuple(sections),
    )
