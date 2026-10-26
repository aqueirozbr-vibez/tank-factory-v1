**Blocked**: `repair_cap` in phase `code_gates`. Gate focused_tests failed after 3 repairs.

Evidence: `uv run pytest -q tests/unit/test_invoice_due_date.py` exited with code 1.

<details><summary>Last output</summary>

```text
FAILED tests/unit/test_invoice_due_date.py::test_zero_terms - assert 1 == 0

```

</details>

Removing `factory:needs-human` starts a new attempt on the same branch with fresh caps.
