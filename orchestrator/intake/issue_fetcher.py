"""Fetch an issue and its thread as provenance-tagged data (PRD §Capability 1, §Risks).

Issue text is never inlined into a system prompt; the planner receives this
rendering as a data block in which every section and comment names its
author and whether that author is trusted.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from orchestrator import constants
from orchestrator.github.client import IssueComment

REQUIRED_SECTIONS = ("Outcome", "Acceptance criteria")
TEMPLATE_SECTIONS = ("Outcome", "Scope", "Non-goals", "Acceptance criteria", "Holdout")
HEADING = re.compile(r"^#{2,3}\s+(.+?)\s*$")
TEST_ID = re.compile(r"tests/acceptance/[\w./\-]+(?:::[\w\[\]\-,]+)?")
PT_MARKERS = (
    " não ",
    " para ",
    " com ",
    " que ",
    " uma ",
    " um ",
    "ção",
    " é ",
    " de ",
    " do ",
    " da ",
)
EN_MARKERS = (" the ", " and ", " with ", " that ", " is ", " should ", " when ", " of ", " to ")


class IssuePort(Protocol):
    async def get_issue(self, number: int, *, task_id: str) -> dict[str, Any]: ...

    async def list_issue_comments(self, number: int, *, task_id: str) -> list[IssueComment]: ...


def is_trusted(association: str | None) -> bool:
    return (association or "").upper() in constants.TRUSTED_ASSOCIATIONS


def detect_language(text: str) -> str:
    padded = f" {text.lower()} "
    pt = sum(padded.count(m) for m in PT_MARKERS)
    en = sum(padded.count(m) for m in EN_MARKERS)
    return "pt-BR" if pt > en else "en"


def parse_sections(body: str) -> dict[str, str]:
    """``## Heading`` or ``### Heading`` blocks; text before the first heading is ``_preamble``."""
    sections: dict[str, list[str]] = {}
    current = "_preamble"
    for line in body.splitlines():
        match = HEADING.match(line)
        if match:
            current = match.group(1).strip()
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@dataclass(frozen=True)
class TaggedComment:
    id: int
    author_login: str
    author_association: str
    trusted: bool
    body: str
    created_at: str


@dataclass(frozen=True)
class TaggedIssue:
    number: int
    title: str
    author_login: str
    author_association: str
    trusted: bool
    body: str
    sections: dict[str, str]
    comments: tuple[TaggedComment, ...]
    labels: tuple[str, ...] = field(default_factory=tuple)

    @property
    def language(self) -> str:
        return detect_language(self.title + "\n" + self.body)

    def missing_required(self) -> list[str]:
        return [name for name in REQUIRED_SECTIONS if not self.sections.get(name)]

    def holdout_ids(self) -> list[str]:
        section = self.sections.get("Holdout", "")
        return list(dict.fromkeys(m.group(0).rstrip(".,;)") for m in TEST_ID.finditer(section)))

    def render_for_agent(self) -> str:
        """The issue as a data block: tagged sections, then tagged comments."""
        lines = [
            f'<issue number="{self.number}" title="{_escape(self.title)}"'
            f' author="{self.author_login}" association="{self.author_association}"'
            f' trusted="{str(self.trusted).lower()}" language="{self.language}">'
        ]
        for name, text in self.sections.items():
            lines.append(
                f'<section name="{_escape(name)}" author="{self.author_login}"'
                f' trusted="{str(self.trusted).lower()}">'
            )
            lines.append(_escape(text))
            lines.append("</section>")
        lines.append("</issue>")
        lines.append("<comments>")
        for comment in self.comments:
            lines.append(
                f'<comment id="{comment.id}" author="{comment.author_login}"'
                f' association="{comment.author_association}"'
                f' trusted="{str(comment.trusted).lower()}" created_at="{comment.created_at}">'
            )
            lines.append(_escape(comment.body))
            lines.append("</comment>")
        lines.append("</comments>")
        return "\n".join(lines)


async def fetch_issue(github: IssuePort, number: int, *, task_id: str) -> TaggedIssue:
    raw = await github.get_issue(number, task_id=task_id)
    comments = await github.list_issue_comments(number, task_id=task_id)
    user = raw.get("user") or {}
    association = str(raw.get("author_association") or "NONE")
    body = str(raw.get("body") or "")
    labels: Sequence[Any] = raw.get("labels") or []
    return TaggedIssue(
        number=int(raw.get("number", number)),
        title=str(raw.get("title") or ""),
        author_login=str(user.get("login") or "unknown"),
        author_association=association,
        trusted=is_trusted(association),
        body=body,
        sections=parse_sections(body),
        comments=tuple(
            TaggedComment(
                id=c.id,
                author_login=c.author_login,
                author_association=c.author_association,
                trusted=is_trusted(c.author_association),
                body=c.body,
                created_at=c.created_at,
            )
            for c in comments
        ),
        labels=tuple(
            str(label.get("name")) if isinstance(label, dict) else str(label) for label in labels
        ),
    )
