# Unit 61: Review findings

## Goal

`ReviewHeader`, `AcTable`, `FindingsList`, `RoundSwitcher` and the review
`ScoreBreakdown` under `/tasks/[taskId]/review` (PRD §Capability 4, §Capability 6;
TRD §5.2 Review findings, §6.1 `GET /tasks/{id}/review?round=`; ui-context.md ·
Component Library · FindingsList, ScoreBreakdown; Layout Patterns · Review
findings).

## Design

Terminal only. `lib/review.ts` reads the task detail (for the title, the PR and
the branch the locations link into) and the round's review; `review_pending`
(409) reads "No review yet", a bad `?round=` names the latest round, and any
other failure is named in a status line. The derivations live beside the read:
`orderedFindings` puts the blocking findings first (a finding blocks when its
own score would put conformance under the policy's accept line), then the
advisory ones, then the dismissed ones, each group strongest first; `acRows`
turns the reviewer's envelope into one row per acceptance criterion (`met`
across the reviewer's findings for that AC, its evidence, the problem and
location when not met) and adds the ACs the policy found unaddressed
(`missing_ac`) as "not addressed"; `headerText` prints "Conformance 44 ·
accept ≥ 70 · round 1 of 2"; `blobUrl` turns `file:line` into the GitHub blob
URL at the task's branch; `reviewProps` builds the review `ScoreBreakdown`
from the strongest finding (base × spec link × evidence × scope, the printed
equation, and "conformance 100 − 56 = 44"), the verdict chip, a conclusion
that names the accept line, the unaddressed ACs or the human reasons, and the
audit override.

The page (a server component; the round is URL state, `?round=`) renders
`RoundSwitcher` (one link per round, the shown round current), `ReviewHeader`
(task link and title, verdict chip, the header line, the reviewer's own
verdict when the policy decided differently, the human reasons, the
unaddressed ACs, the override), then two columns: `AcTable` and
`FindingsList` (Severity, Req., Location, Problem, Adjustments, Score,
Effect; a dismissed row sits at 60% opacity and quotes its Non-goals line;
empty reads "No findings. Every AC met.") on the left and the
`ScoreBreakdown` on the right.

Two orchestrator additions the screen needs and the tables did not spell out,
recorded here: the scored finding payload now carries its four factors
(`base`, `spec_link`, `evidence`, `scope`) beside the printed equation, and
the verdict payload and the review read carry `accept_conformance`, the
policy threshold the verdict was judged against (PRD Capability 4's accept
line, which the header and the conclusion name). `openapi.json` and
`lib/api.types.ts` are regenerated.

## Implementation

- `console/lib/review.ts`; `console/components/{ReviewHeader,AcTable,
  FindingsList,RoundSwitcher}.tsx` with CSS Modules;
  `console/components/review.test.tsx`.
- `console/app/tasks/[taskId]/review/page.tsx` + `page.module.css`.
- `orchestrator/instruments/verdict.py` (payload factors,
  `accept_conformance`), `orchestrator/models/api.py` (`ReviewView`),
  `orchestrator/api/read.py`; `openapi.json`, `lib/api.types.ts`.

## Dependencies

- Unit 60 (the task loader, the page shape); units 53 and 58.

## Verify when done

- [ ] vitest: blocking, advisory and dismissed ordering; the Non-goals line
      quoted on dismissed rows; the AC rows with the unaddressed criteria;
      the header text; the breakdown's factors and conclusion; axe clean
- [ ] `npm run lint`, `typecheck`, `test`, `build` pass; the Python checks
      pass with the payload factors and the accept threshold
