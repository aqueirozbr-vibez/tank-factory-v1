The spec maps acceptance criteria to holdout tests that do not exist in the holdout checkout:

- `tests/acceptance/test_invoice_due_date_holdout.py::test_zero_terms`

Add them under `tests/acceptance/` on the holdout branch, then remove `factory:needs-human` (PRD Capability 2).
