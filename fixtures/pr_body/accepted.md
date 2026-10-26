Factory PR for #418 (acme/atlas-api#418), attempt 1, factory 0.1.0.

**Spec**: [`specs/418-invoice-due-date.md`](https://github.com/acme/atlas-api/blob/agent/418/specs/418-invoice-due-date.md) at `aaaaaaaaaaaa` · risk `risk:low` (score 40.0) · branch `agent/418` at `bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`.

**Review**: verdict `accepted`, conformance 94.0; strongest finding style (30 x 0.5 x 0.4 x 1 = 6); 0 revision round(s).

### Gates

| Gate | Attempt | Result | Evidence |
| --- | --- | --- | --- |
| spec_sections | 1 | pass | 1 check(s) |
| spec_ears | 1 | pass | 1 check(s) |
| spec_ac_holdout_map | 1 | pass | 1 check(s) |
| envelope_schema | 1 | pass | 1 check(s) |
| permissions_enforce | 1 | pass | 1 check(s) |
| focused_tests | 1 | fail | tests/unit/test_invoice_due_date.py::test_zero_terms: assert 1 == 0 \| left side; `uv run pytest -q tests/unit/test_invoice_due_date.py` exit 1 |
| focused_tests | 2 | pass | `uv run pytest -q tests/unit/test_invoice_due_date.py` exit 0 |

### Phases

| Phase | Owner | Harness | Model | Attempt | Status | Tokens in / out / cache read / cache write | Cost (USD) | Session |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| plan | planner | claude_code | claude-opus-5-5 | 1 | pass | 8000 / 900 / 5000 / 0 | 0.2100 | plan-1 |
| spec_gate | orchestrator |  |  | 1 | pass | 0 / 0 / 0 / 0 | 0.0000 |  |
| commit | orchestrator |  |  | 1 | pass | 0 / 0 / 0 / 0 | 0.0000 |  |
| build | builder | claude_code | claude-opus-5-5 | 1 | pass | 18450 / 2210 / 12000 / 640 | 0.4100 | sess-green |
| code_gates | orchestrator |  |  | 1 | pass | 0 / 0 / 0 / 0 | 0.0000 |  |
| commit | orchestrator |  |  | 1 | pass | 0 / 0 / 0 / 0 | 0.0000 |  |
| review | reviewer | codex | gpt-5-codex | 1 | pass | 15230 / 1480 / 9000 / 0 | 0.2000 | rev-ok |

Total cost: $0.8200 over 7 phase rows; tokens in 41680, out 4590.

### Roles

| Role | Harness | Version | Model |
| --- | --- | --- | --- |
| planner | claude_code | 2.1.309 | claude-opus-5-5 |
| builder | claude_code | 2.1.309 | claude-opus-5-5 |
| reviewer | codex | 0.112.0 | gpt-5-codex |

### Advisory findings

- style Minor: Duplicated date arithmetic (ask `@mlima`). (score 6)

### Dismissed by Non-goals

- performance: "Caching of invoice queries."

<details><summary>Spec</summary>

# 418 — Invoice due date

risk: risk:low

## Outcome

Ask `@rsouza` when unsure. An invoice exposes a computed due date so the billing UI can show it
without a migration.

## Scope

- src/billing/invoice.py
- tests/unit/test_invoice_due_date.py

## Non-goals

- Caching of invoice queries.
- Exposing due_date on the list endpoint.

## Acceptance criteria

1. WHEN an Invoice is created with an issued_at date and terms in days
   THE SYSTEM SHALL expose due_date equal to issued_at plus the terms.
2. WHEN the terms are zero days THE SYSTEM SHALL expose due_date equal to
   issued_at.

## Constraints

No ADR applies.

## Open questions

None.

## Test plan

- AC1 -> tests/acceptance/test_invoice_due_date_holdout.py::test_due_date
- AC1 unit -> tests/unit/test_invoice_due_date.py::test_due_date_adds_terms
- AC2 -> tests/acceptance/test_invoice_due_date_holdout.py::test_zero_terms
- AC2 unit -> tests/unit/test_invoice_due_date.py::test_zero_terms_is_same_day

</details>

Rendered by the factory from its trace; no agent wrote this text.
