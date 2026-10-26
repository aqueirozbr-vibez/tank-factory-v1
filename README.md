# Tank Factory v1

Tank Factory v1 turns a GitHub issue into a reviewed pull request. You write the
issue and its hidden acceptance tests and add one label. The factory then plans a
spec, builds it, runs your quality checks and acceptance tests, has a second model
review the change, and opens a pull request for you to merge. Every step, tool call
and dollar is recorded in a tamper-evident trace you can watch in a web console.

```
issue + factory:ready ──► plan (spec) ──► your approval, if risky ──► build
      ──► quality gates ──► review ──► acceptance tests ──► pull request ──► you merge
```

- **Agents run as command-line tools.** The planner and builder use Claude Code;
  the reviewer uses Codex, a different vendor's model, so the review is independent.
- **Code decides, not the model.** Caps, gates, risk scores, the review verdict and
  the budget are enforced by the orchestrator, never left to an agent.
- **Humans keep the gates.** Plans above a risk threshold wait for your approval,
  every merge is yours, and a blocked task explains why in the issue.
- **One repository, one machine.** It runs on your computer or a VM you control,
  against one GitHub repository, with your own model API keys.

## Start here

**[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)** walks you from a fresh clone
to your first merged pull request, starting with a free offline demo.

## What is in this repository

| Path | What it is |
| --- | --- |
| `orchestrator/` | The Python orchestrator: intake, phases, gates, harness adapters, the trace, the API |
| `cli/` | The `factory` command: `serve`, `run-spec`, `dev seed`, `install`, `stop`, `eval` and more |
| `console/` | The Next.js web console: Overview, tasks, review findings, approvals, eval, configuration |
| `deploy/` | Docker Compose for the model proxy, the task image, the agent hook bundle, a systemd unit |
| `docs/` | The product requirements (PRD), the technical design (TRD) and this guide |
| `context/` | The working notes the factory itself was built from: architecture, standards, progress |
| `tests/` | Unit, integration, end-to-end and load tests |

## Requirements

Python 3.12 with [uv](https://docs.astral.sh/uv/), Node.js 20 or later, git, the
GitHub CLI, the Claude Code and Codex CLIs, and API keys for Anthropic and OpenAI.
Docker is optional; it adds a sealed container per task.

## License

[MIT](LICENSE).
