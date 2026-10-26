# Unit 65: Runtime controls and the kill-switch dialog

## Goal

`KillSwitchDialog` (Reason required, focus in Reason, `Esc` cancels, Enter
confirms only when filled, per-process progress), budget raise, concurrency,
admissions pause and resume, incident clear, re-enable; each through the
control endpoints with a confirmation and a reason (PRD §Capability 5 cost
control and kill-switch, §Capability 6; TRD §5.2 Overview client islands and
keyboard map, §6.1 `POST /controls/kill-switch`, `/controls/concurrency`,
`/controls/admissions`, `/tasks/{id}/budget`; ui-context.md · Component
Library · KillSwitchDialog and supporting controls).

## Design

Terminal only. `lib/controls.ts` holds the rules the dialogs show: the 60 s
bound, N between 1 and 10, one raise per task at most 2×, the pill text
"FACTORY_ENABLED=1 · 3 live processes", and why a control is disabled ("no
write access (read)", "already 0 · stopped by @login at time", "raised once
already", "the task is merged"). `KillSwitchDialog` (client) sits first in
the Overview's content so Tab from the header reaches it: the header pill,
the button "Stop factory…" outlined in the fail role (or "Start factory…"
when the flag is 0), a centred dialog that states the effect, needs a one-
line Reason with focus in it ("Recorded as an events row with your login
@login"), `Esc` cancels, Enter confirms only when the Reason is filled,
"Stopping 3 processes · 41 s left" counts down from 60 s while the fan-out
runs, and the success line names the events row and the login. Re-enabling
re-admits nothing. `ControlsPanel` (client, in the Overview rail) sets N and
pauses or resumes admissions, each with a Reason, clearing the open incident
on resume when the operator keeps the box ticked. `BudgetRaiseDialog`
(client, under the task budget meter) raises once, within 2×, with a Reason.

Each control posts to a BFF proxy (`/api/console/controls/{kill-switch,
concurrency,admissions}`, `/api/console/tasks/[taskId]/budget`) that follows
the BFF order with the matching authz action and forwards to the
orchestrator with the actor and an Idempotency-Key; every refusal is the
orchestrator's own (403, 409 already in state or already raised, 422
bounds). The orchestrator writes the `events` row with the login and, for the
kill-switch, performs the fan-out.

Two reads the dialog needs that the TRD's tables did not carry, recorded
here: `/healthz` now carries `live_processes` (open rows in `processes`),
and the installation read carries `open_incident_seq` (the newest `incident`
event no admissions resume has cleared) and `last_kill_switch` (actor, time,
state and reason of the last kill-switch control). `openapi.json` and
`lib/api.types.ts` are regenerated.

The Playwright check (`e2e/controls.spec.ts`, last in the set because the
kill-switch cancels the seeded running task) runs against
`tests/e2e/serve_seeded_api.py`, which now wires a `KillSwitch` without a
supervisor (the flag, the rows and the event; no process tree or container
here): a budget raise appears as a control event with the login; the
concurrency and admissions controls read back their events rows; stopping
from the console flips the flag, cancels the running task and reads back the
events row within 60 s; starting again flips it back. The real SIGTERM
bound on a live fake task was measured in the phase 3 gate (spec 46).

## Implementation

- `console/lib/controls.ts`; `console/components/{KillSwitchDialog,
  ControlsPanel,BudgetRaiseDialog}.tsx` with CSS Modules;
  `console/components/controls.test.tsx`; `console/e2e/controls.spec.ts`.
- `console/app/page.tsx` (the kill-switch first, the controls in the rail),
  `console/app/tasks/[taskId]/page.tsx` (the raise under the meter).
- `console/app/api/console/controls/{kill-switch,concurrency,admissions}/
  route.ts`, `console/app/api/console/tasks/[taskId]/budget/route.ts`.
- `orchestrator/api/app.py` (`live_processes`), `orchestrator/models/api.py`
  (`KillSwitchNote`, `open_incident_seq`, `last_kill_switch`),
  `orchestrator/api/read.py`; `tests/e2e/serve_seeded_api.py`;
  `openapi.json`, `lib/api.types.ts`.

## Dependencies

- Units 40 (kill-switch), 55 (controls), 57, 59, 60, 62 (BFF, `postJson`).

## Verify when done

- [ ] Playwright: stopping from the console flips the flag, cancels the
      running task and reads back the events row with the login within
      60 s; starting again flips it back; concurrency, admissions and a
      budget raise each read back their events row with the login
- [ ] vitest: the rules; the dialog's disabled reasons, Reason gate, focus
      and success line; the panel's N and Reason gate and the open incident;
      the raise bounds; axe clean
- [ ] `npm run lint`, `typecheck`, `test`, `build` pass; the Python checks
      pass with the three read additions
