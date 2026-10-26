# Unit 29: Comment templates

## Goal

Every comment the factory posts on GitHub is rendered from a Jinja2
template under `orchestrator/templates/comments/` from named fields taken
from trace rows and the spec, never from agent free text (architecture.md ·
Templates; TRD §8 GitHub-side screens). The PRD's named screens:
Queue-Comment, Plan-Review-Comment, Blocked-Comment,
Review-Findings-Comment.

## Design

No UI beyond the comments themselves. Texts stay byte-identical to the
unit 20 to 28 messages so their tests and the people reading issues see no
change; two comments are new: `blocked` (reason, phase, command, exit code,
output tail, the PRD's resume rule for that reason) and `budget_stop`.

## Implementation

### orchestrator/templates/__init__.py

- `environment()`: `StrictUndefined`, `trim_blocks`, `lstrip_blocks`, no
  autoescape (Markdown), a `nomention` filter that wraps `@login` in
  agent-authored text in backticks so GitHub does not notify anyone.
- `render_comment(name, **fields)`: refuses a field the template does not
  declare (`ValueError`) and a field it misses (`UndefinedError`); the
  result is stripped.
- `comment_templates()`: the template names on disk.

### orchestrator/templates/comments/*.md.j2

`refused_actor`, `queued`, `cancelled`, `open_questions` (English and
pt-BR), `missing_holdout`, `plan_review`, `approval_ignored`,
`review_findings`, `blocked`, `budget`.

### orchestrator/intake/messages.py

- The same functions as before, now building the field set and calling
  `render_comment`; new `blocked(reason, *, phase, summary, command,
  exit_code, output_tail)` with the next step per PRD §Capability 5 ·
  Resume, and `budget_stop(...)`.

### Dependencies

- `jinja2` (new runtime dependency; templates ship inside the package).

## Verify when done

- [ ] Snapshot per template under `fixtures/comments/` matches the
      rendered text
- [ ] An unknown field is refused; a missing field is refused; the template
      set on disk equals the renderer set
- [ ] `@login` in spec text or a finding is neutralised; a CODEOWNER
      mention in the plan-review comment stays live
- [ ] The next step of a `protected_path` block says it never resumes; of a
      `repair_cap` block, that removing the label starts a new attempt
- [ ] The four quality commands pass and the earlier message tests still do
