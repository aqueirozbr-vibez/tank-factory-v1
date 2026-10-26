# Unit 19: Reconciliation poller

## Goal

Keep admission working behind a firewall and repair missed deliveries: a
60 s poller reads GitHub and emits the same `NormalisedEvent`s the webhook
path emits (PRD §Architecture · GitHub event intake; TRD §2.0, §4.5).

## Design

No UI. Visible result: with recorded responses, one poll yields the label,
merge, review and check-run events as `poll`-sourced rows; a second poll
of the same data yields only duplicates and nothing new in the inbox; a
changed `main` head yields one `push` event with the changed paths.

Idempotency note. Webhook delivery ids are GUIDs the poller cannot know,
so poll ids are reconstructed from the objects themselves:
`issue_event:<id>`, `pr_merged:<number>:<sha>`, `review:<id>`,
`check_run:<id>`, `push:<sha>`. A webhook and a poll of the same event may
both be rows in `github_events`; zero duplicate side effects is guaranteed
by the state machine (one task per issue, approvals bound to the spec sha,
status-guarded cancel and resume), not by this table. Re-polls of the same
object are one row.

## Implementation

### migrations/0003_poll_cursors.sql

- `poll_cursors(name, value, updated_at)`.

### orchestrator/intake/poller.py

- `Poller(client, writer, inbox, interval_s, clock, main_branch)`.
- `poll_once()` → `PollReport(started_at, new_events, duplicates, errors)`:
  - cursor `events` (default 24 h back), queried with a 120 s overlap;
  - repository issue events → `issue_labeled` / `issue_unlabeled`;
  - closed pulls sorted by update → `pull_request_merged` for `agent/*`
    heads merged after the cursor;
  - reviews and check runs for every task in `pr_open` →
    `review_submitted`, `check_run_completed`;
  - `main` head compared with the `main_sha` cursor → `push` with the
    files from the compare API;
  - each sub-poll is isolated: a `GitHubApiError` writes an `error` event
    and the others still run;
  - every event goes through `record_delivery`; new, non-ignored events
    reach the inbox; the cursors and `last_poll_at` advance.
- `run(stop)` ticks every `interval_s` until the stop event.

### GitHub client and writer additions

- `list_issue_events`, `list_pulls`, `list_reviews`, `list_check_runs`,
  `compare_files`.
- `get_poll_cursor`, `set_poll_cursor`, `open_pull_requests`,
  `set_task_pr`.

## Dependencies

- None new.

## Verify when done

- [ ] First poll: five new events (two labels, one merge, one review, one
      check run) with reconstructed ids and `source = poll`; the `main_sha`
      cursor is set without a push event
- [ ] Second poll with the same data: zero new events, five duplicates,
      nothing added to the inbox
- [ ] A new `main` head yields one `push` event listing the compared files
- [ ] An endpoint failing after retries writes an `error` event and the
      other sub-polls still deliver
- [ ] `run` stops on the event; `/healthz` would show `last_poll_at`
- [ ] The four quality commands pass
