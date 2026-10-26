# Unit 43: Governed PRs and branch protection

## Goal

A `risk:governed` PR opens with the governed path's CODEOWNER requested as
reviewer, and branch protection requires that review before merge; the
installer verifies the protection and refuses to go live otherwise (PRD
§Capability 2 · `risk:governed`, §Capability 5 · Delivery; TRD §7.5
"request CODEOWNER if governed").

## Design

Terminal only. The request side exists since unit 30 (`DeliverPhase`
requests the owners the runner resolves from `CODEOWNERS` at `main`
through `PlanApprovals.owners_of`); this unit proves it from a committed
`CODEOWNERS` file and adds the protection check. The repository rules
API (`GET /repos/{o}/{r}/rules/branches/{branch}`) answers with the App's
Metadata permission, so a ruleset `pull_request` rule with
`require_code_owner_review` is the primary evidence; classic branch
protection (`GET .../branches/{branch}/protection`) needs Administration
read, which the App does not hold, so a 403 there counts as unreadable and
never as required. `factory protection --org --repo [--branch]` runs the
check, appends the verdict to the install log and exits 1 when the review
is not required; `factory install` (unit 44) calls it and refuses to go
live on a 1.

## Implementation

- `orchestrator/github/protection.py`: `codeowner_review_required`,
  `check_branch_protection`, `ProtectionReport`.
- `orchestrator/github/client.py`: `get_branch_rules`,
  `get_branch_protection` (200, 403 and 404 handled), `get_repository`.
- `cli/protection.py` and the `factory protection` command.

## Dependencies

- Units 25 and 33. No new package.

## Verify when done

- [ ] A committed `CODEOWNERS` resolves the governed path's owners and the
      opened PR requests exactly them (login and team)
- [ ] A ruleset requiring code owner review passes; classic protection
      passes; a 403 on classic protection is unreadable; nothing → refused
- [ ] `factory protection` writes the verdict to the install log and exits
      0 or 1; missing App settings exit 2
- [ ] The four quality commands pass
