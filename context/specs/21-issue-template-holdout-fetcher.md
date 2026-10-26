# Unit 21: Issue template, holdout checkout and issue fetcher

## Goal

Give the planner its only input and the factory its hidden test set
(PRD §Capability 1, 2, 3): an issue form with the five headings, a
checkout of the holdout tests that no agent workspace ever contains, and
one script that fetches the issue and its comments and tags every section
with who wrote it, so issue text reaches an agent as data and never as
instructions.

## Design

No UI. Visible result: the recorded issue parses into tagged sections with
trusted flags and holdout ids; a clone of the product repository exposes
its acceptance test ids and refreshes from origin; the template is valid
GitHub issue-form YAML and the seed command installs it.

## Implementation

### deploy/product-repo/.github/ISSUE_TEMPLATE/factory-task.yml

- GitHub issue form with required textareas Outcome, Scope, Non-goals,
  Acceptance criteria and Holdout (ids `outcome`, `scope`, `non_goals`,
  `acceptance_criteria`, `holdout`). GitHub renders them as `###`
  headings, which the fetcher accepts alongside `##`.
- `factory dev seed` copies it into the scratch repository.

### orchestrator/workspace/holdout.py

- `HoldoutCheckout(origin, path, branch)`: `ensure()` clones once,
  `refresh()` fetches and hard-resets to `origin/<branch>` (the default
  branch when none is configured) and returns the head sha; `tests_dir`,
  `test_ids()` (`tests/acceptance/<file>::<function>`), `has_test(id)`,
  `copy_into(workspace)` for the full-suite phase, `assert_outside(dirs)`
  as a guard against ever living under a worktree.
- `FactorySettings.holdout_branch` (optional).

### orchestrator/intake/issue_fetcher.py

- `fetch_issue(github, number, task_id) -> TaggedIssue`: title, author
  login and association, `trusted` (OWNER, MEMBER, COLLABORATOR), sections
  parsed from `##`/`###` headings, comments each tagged the same way,
  `language` (`pt-BR` or `en` by a marker heuristic), `missing_required()`
  (Outcome, Acceptance criteria), `holdout_ids()` from the Holdout section.
- `render_for_agent()` emits the issue as tagged data blocks with `<`
  escaped inside content so no comment can close a tag or impersonate a
  section.

## Dependencies

- None new.

## Verify when done

- [ ] The recorded issue and comments parse into five sections, the MEMBER
      author and comment are trusted, the NONE comment is not, the holdout id
      is extracted, and nothing is missing; a body without Acceptance criteria
      reports it missing; `###` headings parse like `##`
- [ ] Portuguese text yields `pt-BR`, English yields `en`
- [ ] The rendered block escapes a `</section>` injected in a comment and
      carries author and trusted attributes
- [ ] A holdout clone of the product repository lists
      `tests/acceptance/test_holdout.py::test_h`, `has_test` agrees,
      `refresh()` picks up a new test pushed to origin, `copy_into` copies only
      `tests/acceptance`, and `assert_outside` refuses a path under worktrees
- [ ] The template is valid YAML with the five required fields and the seed
      installs it
- [ ] The four quality commands pass
