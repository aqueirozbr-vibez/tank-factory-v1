# Unit 50: Slack notifications

## Goal

Incoming-webhook notices for a plan review waiting and for a stuck run;
notification only: no code path treats a Slack response as an approval
(PRD §Capability 2 · Plan approval, §Capability 5 · Stuck runs, §Scope ·
Phase 4; TRD §2.0 Notifications, §6.4 Stuck run, §3.3 egress to Slack).

## Design

Terminal only. `orchestrator/notify/slack.py` builds a `Notice` (kind,
task id, title, ordered fields, footer) and posts it as the webhook body
(`text` plus one `section` and one `context` block, nothing interactive).
`plan_review_notice` carries the issue, risk class, spec path, who may
approve (a write-access user, or the CODEOWNERs of the governed path) and
the label to add; `stuck_run_notice` carries the process, the idle time
against the 15-minute threshold and the last event time. `SlackNotifier`
posts to `FACTORY_SLACK_WEBHOOK_URL` once per notice, never raises, and
writes one `log` event per attempt (`notify: slack`, kind, ok, status,
error class or Slack's short reply); the URL embeds a secret, so it never
enters the trace, a log line or an exception message. `PlanApprovals`
holds the optional notifier and `PlanApprovalPhase` sends the plan-review
notice beside the GitHub comment, under the same `awaiting_logged` guard,
so a restart does not repost; the wait still ends only on the
`factory:plan-approved` label event. `Watchdog` gains an `on_stuck` hook
that the service points at the notifier, and the service now runs the
watchdog loop (unit 14 built it; nothing ran it). Without the URL the
notifier is `None` and nothing changes.

## Implementation

- `orchestrator/notify/__init__.py`, `orchestrator/notify/slack.py`:
  `Notice`, `NotifierPort`, `SlackNotifier`, `plan_review_notice`,
  `stuck_run_notice`.
- `Settings.slack_webhook_url` (`FACTORY_SLACK_WEBHOOK_URL`), `.env.example`.
- `PlanApprovals(notifier=)`, `PlanApprovalPhase` sends the notice;
  `Watchdog(on_stuck=)` awaits the hook per newly flagged process;
  `service.py` builds the notifier, the watchdog and the loop.
- Snapshots: `fixtures/notify/plan_review_waiting.json`,
  `fixtures/notify/stuck_run.json`.

## Dependencies

- Units 25 (plan approval waiter) and 14 (watchdog). `httpx` (present).

## Verify when done

- [ ] Both payloads match their snapshots
- [ ] A 200 reply logs ok, a 500 and a connection error log the failure
      without the URL anywhere in the trace, and `send` never raises
- [ ] The waiter posts one notice, none on a restart, and stays waiting
      after Slack answers until the label event arrives
- [ ] The watchdog loop calls the hook once per stuck process
- [ ] The four quality commands pass
