# Unit 46: Phase 3 gate run

## Goal

The four phase 3 gate conditions, each with its evidence (PRD §Scope ·
Phase 3; build plan): the builder fails 3 of 3 attempts to push, call `gh`
or reach the internet; an orchestrator restart mid-task opens 0 duplicate
PRs across 5 restarts; the same workflow completes 5 issues on each
harness; the kill-switch stops a live task within 60 s.

## Design

Terminal only. `tests/integration/test_phase3_gate.py` records the four
conditions on this machine: condition 1 on Docker Desktop (opt-in) with a
real task container on the internal `factory-tasks` network and the hook
bundle mounted, nine hook attempts and three network and three `gh`
attempts; condition 2 by running unit 45's five-crash scenario; condition
3 through the composition root with the real Claude Code and Codex
adapters driven by CLI-shaped streams synthesised from the seed scripts
(the adapters' argv, stdin and parsers are exercised; only the model is
absent), five issues with the builder on each harness and the reviewer on
the other vendor; condition 4 through the composition root with a sleeping
builder and `KillSwitch.disable`. Each test prints one `GATE-EVIDENCE`
line; the tracker carries the numbers. The paid run on the pilot VM with
the real CLIs is the user's; the runbook below names it.

## Runbook for the pilot VM (the user, paid)

1. `factory install --repo-clone /var/lib/factory/repo --org <org> --repo
   <repo> --issues a b c --public-host ... --console-host ...` with
   `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and the App settings exported.
2. Condition 1: label one issue `factory:ready`; in its builder phase the
   `tool_call` rows carry `denied: true` for any push, `gh` or `curl` the
   agent tries, and the nftables log (`journalctl -k | grep
   factory-egress`) shows the dropped packets; three such attempts across
   tasks make the 3 of 3.
3. Condition 2: `systemctl restart factory` five times while a task runs;
   `SELECT COUNT(*) FROM tasks WHERE pr_number IS NOT NULL` stays one per
   task and the issue carries no duplicate comment.
4. Condition 3: five issues with the shipped roster, then five with
   `builder.harness: codex` in `.factory/roster.yaml` (and the reviewer
   on Claude Code); ten `pr_open`.
5. Condition 4: `factory stop --reason "gate"` during a build; the control
   event's `elapsed_s` is under 60.

## Dependencies

- Unit 45. No new package.

## Verify when done

- [ ] Offline: five issues `pr_open` with the builder on Claude Code and
      five with the builder on Codex, agent phase rows carrying each
      adapter's name and version
- [ ] Offline: five crashes, one PR, no comment
- [ ] Offline: the kill-switch stops a live builder within 60 s through
      the composition root
- [ ] Opt-in on Docker: 9 of 9 hook denials, 3 of 3 internet failures,
      3 of 3 `gh` failures inside a task container on the internal network
- [ ] The four quality commands pass; the tracker records the evidence
