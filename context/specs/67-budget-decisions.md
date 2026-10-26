# Unit 67: Budget decisions (amendment, 2026-10-25)

## Goal

Apply the user's answers to the four open budget questions (tracker · Open
Questions; PRD §Capability 5 · Budgets, §Non-functional · Cost control; TRD
§6.3): the install derivation never drops below the industry per-task cap;
the median rule stays as the PRD writes it; an eval replay has its own
ceiling instead of competing with the day's cap; the console's Install dry
run panel shows the derivation itself.

## Decisions and their sources

1. **Install derivation: the industry per-task cap as the floor.** The user
   asked for the cap in common use to be found and applied. The survey
   (2026-10-25, web): per-task caps in CI-style use sit at US$3 to 5
   (`claude -p --max-budget-usd 5.00` and a US$3 example with a turn
   limit); measured per-task spend on real work is US$0.80 to 5 for Claude
   Code on API pricing; SWE-bench Verified attempts with current frontier
   models cost US$0.68 to 0.75 per task (Opus 4.5, high reasoning), US$0.33
   (Haiku 4.5), about US$1.3 for a Claude Code + Sonnet 4.5 run, and US$0.67
   to 1.98 per task across scaffolds on a 100-task subset; Devin bills US$2.25
   per ACU and a typical bug fix is 2 to 3 ACU, US$4.50 to 6.75. The figures
   use different definitions (per attempt, per resolved task, per PR), so
   the cap, not the average spend, is the number to adopt: US$5 per task.
   `INDUSTRY_PER_TASK_CAP_USD = 5.0`; `derive_budget` keeps 2× the highest
   observed cost when that is higher and returns the cap with a reason
   naming both when it is lower; the record carries `floor_usd`. With the
   shipped phase caps (1.50 / 3.00 / 1.50) a US$5 task budget admits every
   phase of a typical task (plan ≈ 0.8, build ≈ 1.9, review ≈ 0.5) and
   refuses the reviewer only when the planner and builder both ran to their
   caps, which the budget block then reports.
   Sources: [Tembo, best LLM for coding 2026](https://www.tembo.io/blog/best-llm-for-coding),
   [SSOJet, cost per task](https://ssojet.com/blog/cheapest-ai-coding-models),
   [Codesota, SWE-bench guide](https://codesota.com/agentic/best-for-swe-bench),
   [Princeton HAL SWE-bench Verified](https://hal.cs.princeton.edu/swebench),
   [vexp SWE-bench subset](https://benchmarklist.com/benchmarks/vexp_swe_bench/),
   [How to cap a Claude Code loop's spend](https://youcanbuildthings.substack.com/p/how-to-cap-a-claude-code-loops-spend),
   [Investigating Claude Code's budget cap](https://linuxjedi.co.uk/when-the-docs-fall-short-investigating-claude-codes-budget-cap/),
   [Claude Code cost, March 2026](https://www.morphllm.com/claude-code-cost),
   [Cloud coding agents compared (Devin ACU)](https://ssojet.com/blog/best-cloud-coding-agents),
   [Tokenomics: pricing of AI coding agents](https://dannguyenhuu.substack.com/p/tokenomics-1-the-pricing-evolution).
2. **Median rule: no floor.** Unit 49 stays as written: from day 8 the
   budget is 2× the trailing 30-day median, and a cheap first week can set
   it under the planner's cap until a task's budget is raised. Recorded as
   the user's choice; nothing changes in code.
3. **Eval replay: its own ceiling.** The replay writes into a scratch trace
   whose installation row inherited N × 3 × budget as a daily cap; the
   scratch row now takes items × the per-task budget (`TraceWriter.
   set_daily_cap`, a direct ceiling the production trigger never sees), so
   a 24-item set fits without raising N, and the run's closing log event
   carries `cap_usd` and `spent_usd`. Production tasks keep the daily cap
   untouched.
4. **The derivation on the installation read.** `InstallationView.
   last_dry_run` (a `DryRunNote`: time, outcome, the issues with their
   costs and blocked reasons, completed of required, highest cost, factor,
   budget, floor, reason) comes from the log event the dry run writes; the
   console's `InstallDryRun` panel lists the issues and the equation.

## Implementation

- `orchestrator/constants.py`, `orchestrator/budget/dry_run.py`,
  `orchestrator/trace/writer.py` (`set_daily_cap`),
  `orchestrator/eval/runner.py`, `orchestrator/models/api.py`,
  `orchestrator/api/read.py`; `openapi.json`, `console/lib/api.types.ts`.
- `console/components/InstallDryRun.tsx`, `console/components/dry-run.test.tsx`.
- `context/project-overview.md` (the floor), `context/specs/00-build-plan.md`
  (amendments section), the tracker.
- Existing tests that asserted the un-floored figure now assert the floor:
  `tests/unit/test_budget_service.py` (0.68 → 5.00 with `floor_usd`),
  `tests/unit/test_install.py` (2 × 0.38 = 0.76 → 5.00), `tests/unit/test_cli.py`
  (2 × 0.35 = 0.70 → 5.00, the CLI prints `$5.00`); the phase 4 gate test's
  `cheap_caps` docstring names the floor (its caps still fit).
- `tests/conftest.py` points `FACTORY_ENV_FILE` at an empty file for the
  session: the CLI tests call `main()`, which loads the operator's `app/.env`
  (new this day) into the process, and the export route test then read a
  key path that does not exist. The suite no longer depends on `.env`.

## Dependencies

- Units 44 (dry run), 47 (eval runner), 52, 63.

## Verify when done

- [ ] A dry run at 0.32 / 0.34 derives US$5.00 with the floor named; at 3.10
      it derives 6.20
- [ ] A replay's scratch installation shows items × budget as its cap and
      the run's log event carries `cap_usd` and `spent_usd`
- [ ] The installation read carries the last derivation; the panel lists
      the issues; `npm run lint`, `typecheck`, `test`, `build` and the
      Python checks pass
