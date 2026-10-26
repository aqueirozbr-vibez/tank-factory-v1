```markdown
# <issue number> — <short title>

risk: (left blank; code fills the class)

## Outcome

One or two sentences: what is true when this is done.

## Scope

- <path or module the change may touch>, one per line.

## Non-goals

- <what this task must not do>, one per line.

## Acceptance criteria

1. WHEN <condition> THE SYSTEM SHALL <observable behaviour>.
2. WHEN ... THE SYSTEM SHALL ...

## Constraints

- <ADR file name, e.g. docs/adr/0007-billing-dates.md>, or exactly:
  No ADR applies.

## Open questions

None.
(or one question per line that a human must answer before the build)

## Test plan

- AC1 -> <holdout test id from the issue, e.g. tests/acceptance/test_x.py::test_y>
- AC1 unit -> tests/unit/test_<name>.py::test_<case> (the builder creates it)
- AC2 -> ...
```

Gate rules applied to this file by code, in order: all seven section headings
present; at least one numbered criterion in WHEN ... THE SYSTEM SHALL ... form;
every criterion id present in the Test plan and mapped to at least one holdout
test id that exists in the holdout checkout; Constraints cite an ADR or state
"No ADR applies." A failure is sent back to you with the violation list, at
most twice.
