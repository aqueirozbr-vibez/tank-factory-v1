**Review stopped**: verdict `human_decision_needed`, conformance 30.0.
Why a person decides: third_revision (a third `revision_needed` after 2 revisions).
Strongest finding: AC2 (70 × 1 × 1 × 1 = 70).

Findings:
- AC2 Major at `src/billing/invoice.py:14`: Zero terms does not return issued_at (ask `@mlima`). (score 70.0)
- style Minor: Duplicated date arithmetic. (score 6.0)

Dismissed by Non-goals:
- performance: "Caching of invoice queries."

Prior verdicts: revision_needed, revision_needed.

A person with write access decides: fix the findings on the branch or close the issue, then remove `factory:needs-human` to resume.
