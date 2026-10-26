# Unit 60: Task detail and live trace

## Goal

`TaskStrip`, `PhaseTimeline`, `GateLadder` with `EvidenceDrawer`,
`RepairRounds`, `EnvelopeViewer`, `ChainFooter`, the task budget `Meter`,
`ScoreBreakdown` (risk), `TraceTable` with the SSE tail (pause, resume by
`seq`, 4 s poll fallback, "Tail behind" warning), `CopyJson` (PRD
§Capability 6; TRD §3.4, §4.3, §5.2 Task detail, §6.1; ui-context.md ·
Component Library, Layout Patterns · Task detail).

## Design

Terminal only. `lib/task.ts` makes the Task detail's reads: the detail
first (its chain footer now carries the head seq), then in parallel the
newest page of events (the first paint shows the last page, TRD §3.4),
the envelopes, the gate results, the `log` and `control` events that hold
the risk breakdown and any override, and the queue when the task is
queued ("Queued, n ahead"); each failure is named in a status line. The
page (a server component under `/tasks/[taskId]`) renders `TaskStrip`
(status and risk chips, issue, spec, branch and PR links), `PhaseTimeline`
(one row per phase on a shared axis: solid accent for an agent phase, a
hairline outline for a code step, a 135° hatch while a human holds it; a
running phase extends to now), then two columns: `GateLadder` with its
`EvidenceDrawer` to the right (below at 1280) and `RepairRounds` on the
left, the task budget `Meter` and the risk `ScoreBreakdown` on the right;
then `EnvelopeViewer`, `TraceTable` and `ChainFooter`.

`GateLadder` lists the gates as run; after a failure the gates the phase
would have run next (the orchestrator's own order: format, lint,
typecheck, focused tests for the code gates; the four spec gates; the
envelope gates) read `–` skipped and carry no button; the first failure's
drawer opens with the command, exit code, attempt ("build 1 · repair 1 of
3"), `gate_results` id and time and the kept output; `Esc` closes it.
`EnvelopeViewer` has one tab per agent and one per builder attempt
(Radix Tabs); an invalid envelope shows its schema error paths and the
raw text; an empty tab reads "No envelope; phase did not return."
`TraceTable` is the client island: `useTail` opens an `EventSource` on the
console's SSE proxy from the last `seq`, falls back to a 4 s poll of the
console's events proxy when the stream fails, shows "Tail behind by N s"
when the newest row is older than 5 s while the tail is live, tints a new
row for 2 s, pauses and resumes without losing rows (`p` toggles), keeps
a window of 400 rows in the DOM and loads earlier pages (`before=` cursor)
as the list reaches its end or on the "Earlier rows" button. The chain
footer reads "chain verified · n records · head …" from the orchestrator's
verify routine.

Three orchestrator additions the TRD's design needs and its tables did not
spell out, recorded here rather than invented silently: `before=<seq>` on
`GET /tasks/{id}/events` pages backwards from the head, newest first (TRD
§3.4 "the first paint shows the last page"); `head_seq` on the chain
footer gives the client its starting cursor; the risk breakdown payload
now carries `instrument_version` so the `ScoreBreakdown` can show the
instrument version the UI context names. `openapi.json` and
`lib/api.types.ts` are regenerated.

Playwright (`e2e/task-detail.spec.ts`) runs against `tests/e2e/
serve_seeded_api.py`, which seeds `acme/atlas-api#7` with a full phase
history, a gate ladder failing at `lint` on the first code-gates run,
three envelopes (one invalid), a risk breakdown and 10,000 `tool_call`
rows, and appends one row every 2 s: the page paints under 2 s p95, a new
row appears within 5 s, `p` pauses and resumes the tail, the skipped gates
carry no button and the first failure's evidence is open, and the earlier
pages load to the start of the trace.

## Implementation

- `console/lib/task.ts`, `console/lib/hooks/useTail.ts`.
- `console/components/{TaskStrip,PhaseTimeline,GateLadder,RepairRounds,
  EnvelopeViewer,ChainFooter,CopyJson,TraceTable}.tsx` with CSS Modules;
  `console/components/task-detail.test.tsx`.
- `console/app/tasks/[taskId]/page.tsx` + `page.module.css`;
  `console/app/api/console/tasks/[taskId]/{events,stream}/route.ts`.
- `orchestrator/api/read.py` (`before=` cursor, `head_seq`),
  `orchestrator/models/api.py`, `orchestrator/instruments/risk.py`
  (`instrument_version`); `openapi.json`, `lib/api.types.ts`.
- `console/e2e/task-detail.spec.ts`; `tests/e2e/serve_seeded_api.py`
  (the live task); `@radix-ui/react-tabs`.

## Dependencies

- Unit 59 (the shell, the Playwright setup and the seeded API server);
  units 53, 55, 57, 58.

## Verify when done

- [ ] Playwright: 10,000 trace rows paint under 2 s p95; a new row appears
      within 5 s; `p` pauses the tail; gates after a failure are not
      clickable; the earlier pages load to the start of the trace
- [ ] vitest: the ladder's skipped rungs, the drawer, the tail's merge,
      lag, pause and resume, the envelope tabs, the chain text; axe clean
- [ ] `npm run lint`, `typecheck`, `test`, `build` pass; the Python checks
      pass with the cursor, the head seq and the instrument version
