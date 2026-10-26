# Unit 62: Approvals, audit and label writes

## Goal

`PlanQueue`, `SpecView` with the AC→holdout existence table, `ApprovalCard`
writing `factory:plan-approved` under the user's token through the BFF (409
when the spec sha changed), `QuestionsTable`, `AuditStrata`, `AuditTable`,
`AuditDecisionRow`, `RiskOverrideDialog`; cancel and resume as label removals
(PRD §Capability 2 approvals and questions, §Capability 4 audit, §Capability 6;
TRD §5.2 Approvals, §6.1 `/approvals/plans`, `/approvals/questions`,
`/audit/queue`, `POST /audit/{task_id}`, `POST /tasks/{id}/risk-override`,
BFF `POST /api/console/labels`; ui-context.md · Component Library ·
ApprovalCard; Layout Patterns · Approvals).

## Design

Terminal only. `/approvals?tab=plans|questions|audit` is one page with URL
tabs. `lib/approvals.ts` reads the three queues in parallel, splits the spec
into its `##` sections, turns the spec gate's `spec_ac_holdout_map` checks
into the AC→holdout table (present or missing per criterion), counts the
audit strata (low 10% sampled, high and governed every PR) and decides what
the signed-in viewer may do with each plan: the action is `plan_approve`, or
`governed_approve` for a `risk:governed` task; the authz table (unit 54)
answers with the viewer's permission and, for governed paths, whether the
viewer is a CODEOWNER; a disabled button names its reason ("no write access
(read)", "not a CODEOWNER of src/billing/", "the spec is not committed yet",
"sign in to approve").

CODEOWNERS is read the way the orchestrator reads it: `lib/codeowners.ts`
mirrors `orchestrator/github/codeowners.py` (last matching rule wins,
`@login` case-insensitive, teams and e-mails never match in v1), and the file
comes from GitHub's Contents API at `main` under the user's own token
(`CODEOWNERS`, `.github/CODEOWNERS`, `docs/CODEOWNERS`), cached 60 s per
repository in the console process.

`ApprovalCard` (client) posts `{task_id, issue, add: "factory:plan-approved",
spec_sha}` to `POST /api/console/labels`; the handler follows the BFF order
with an action resolved from the body (`cancel` for removing
`factory:ready`, `resume` for removing `factory:needs-human`, else the plan's
approval action), checks CODEOWNERS for a governed approval, refuses with
429 past 30 label writes a minute per login, with 409 `spec_changed` when the
task's spec sha no longer matches the one the page showed, and with 400 when
the issue does not belong to the task; then it writes the label with the
user's token and GitHub's refusal passes through unchanged. Nothing is
recorded by the console: the orchestrator's approval waiter records the
`approvals` row bound to the spec sha when it sees the label event, so the
card's success line says so and refreshes. `a` focuses the first open plan's
button; the footer reads "The GitHub label is the approval."

`AuditDecisionRow` (client) needs a decision and a Reason and posts to
`/api/console/approvals/audit/[taskId]`, which proxies `POST /audit/{task_id}`
with the actor and an Idempotency-Key; the orchestrator keeps one decision
per task per month and answers 404 or 409. `RiskOverrideDialog` (client,
Radix Dialog, focus in Reason, `Esc` cancels) raises a `risk:high` plan to
`risk:governed` through `/api/console/approvals/risk-override/[taskId]`,
which proxies `POST /tasks/{id}/risk-override`; it is mounted on the plan
queue, where the tech lead reads the plan. `QuestionsTable` shows each
blocked task's questions with `lang="pt-BR"` or `"en"` on the cell and the
issue to answer on. `lib/bff.ts` gained the action resolver, hands the
action to the act step, and passes an orchestrator refusal through with its
own code; `lib/api.ts` gained `postJson` (actor header, Idempotency-Key, no
cache); `lib/ratelimit.ts` holds the console's own per-login write limits
the TRD names.

The build plan's first check, an approval on GitHub with the test user as
actor and the `approvals` row bound to the spec sha, needs the GitHub App
user authorisation and a scratch repository (spec 57 runbook; the phase 2
GitHub gate the user holds) and is recorded under the user's items; the
second, a non-CODEOWNER seeing the governed button disabled with the
reason, is a vitest here.

## Implementation

- `console/lib/{approvals,codeowners,ratelimit}.ts`; `console/lib/{api,bff,
  github}.ts` extended; tests `lib/{codeowners,ratelimit,bff}.test.ts`,
  `components/approvals.test.tsx`.
- `console/components/{PlanQueue,SpecView,ApprovalCard,QuestionsTable,
  AuditTable (AuditStrata),AuditDecisionRow,RiskOverrideDialog}.tsx` with
  CSS Modules.
- `console/app/approvals/page.tsx` + `page.module.css`;
  `console/app/api/console/labels/route.ts`,
  `console/app/api/console/approvals/{audit,risk-override}/[taskId]/route.ts`.
- `console/lib/task.ts`: `riskProps` takes any object with `risk` so the
  plan queue can reuse the risk `ScoreBreakdown`.

## Dependencies

- Units 55 (controls), 57 (sessions, user token), 58, 60, 61.

## Verify when done

- [ ] vitest: the ordering of what a viewer may do (no session, read, write,
      governed without and with CODEOWNER, uncommitted spec); the card's
      disabled reason, success line, GitHub refusal and spec-changed case;
      the spec sections and AC→holdout rows; the question cell's language;
      the strata; the decision row's Reason rule; CODEOWNERS matching; the
      write limits; the BFF action resolver; axe clean
- [ ] `npm run lint`, `typecheck`, `test`, `build` pass
- [ ] User-run (GitHub App authorisation, scratch repository): an approval
      written from the console appears on the issue with the test user as
      actor, and the orchestrator's `approvals` row binds to the spec sha
