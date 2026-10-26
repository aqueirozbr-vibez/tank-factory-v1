"""Branch protection: CODEOWNER review must be required on the base branch (PRD §Cap. 5).

A ``risk:governed`` PR opens with the governed path's CODEOWNER requested as
reviewer; branch protection is what makes that review mandatory before a
merge. The installer verifies it and refuses to go live otherwise. The
repository rules API answers with the App's Metadata permission; classic
branch protection needs Administration read, so a 403 there counts as
"not readable", never as "required".
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

Source = Literal["ruleset", "branch_protection"]


class ProtectionPort(Protocol):
    async def get_branch_rules(self, branch: str, *, task_id: str) -> list[dict[str, object]]: ...

    async def get_branch_protection(
        self, branch: str, *, task_id: str
    ) -> dict[str, object] | None: ...


@dataclass(frozen=True)
class ProtectionReport:
    branch: str
    required: bool
    source: Source | None
    detail: str

    def record(self) -> dict[str, object]:
        return {
            "kind": "branch_protection",
            "branch": self.branch,
            "required": self.required,
            "source": self.source,
            "detail": self.detail,
        }


def codeowner_review_required(
    branch: str,
    rules: Sequence[Mapping[str, object]],
    protection: Mapping[str, object] | None,
) -> ProtectionReport:
    """A ruleset ``pull_request`` rule or classic protection that requires code owner review."""
    for rule in rules:
        if rule.get("type") != "pull_request":
            continue
        parameters = rule.get("parameters")
        if isinstance(parameters, Mapping) and parameters.get("require_code_owner_review") is True:
            return ProtectionReport(
                branch,
                True,
                "ruleset",
                f"ruleset {rule.get('ruleset_id')} ({rule.get('ruleset_source')}) requires"
                " code owner review",
            )
    if protection is not None:
        reviews = protection.get("required_pull_request_reviews")
        if isinstance(reviews, Mapping) and reviews.get("require_code_owner_reviews") is True:
            return ProtectionReport(
                branch,
                True,
                "branch_protection",
                "classic branch protection requires code owner reviews",
            )
    seen = ", ".join(str(r.get("type")) for r in rules) or "none"
    classic = "absent or unreadable" if protection is None else "without code owner reviews"
    return ProtectionReport(
        branch,
        False,
        None,
        f"no pull_request rule with require_code_owner_review on {branch}"
        f" (rules seen: {seen}; classic protection: {classic})",
    )


async def check_branch_protection(
    client: ProtectionPort, branch: str, *, task_id: str = "install"
) -> ProtectionReport:
    rules = await client.get_branch_rules(branch, task_id=task_id)
    protection = await client.get_branch_protection(branch, task_id=task_id)
    return codeowner_review_required(branch, rules, protection)
