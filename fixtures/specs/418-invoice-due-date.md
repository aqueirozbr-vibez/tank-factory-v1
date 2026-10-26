# 418 — Invoice due date

risk: risk:low

## Outcome

An invoice exposes a computed due date so the billing UI can show it
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
