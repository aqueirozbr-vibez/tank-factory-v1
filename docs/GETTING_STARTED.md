# Getting started with Tank Factory v1

This guide takes you from a fresh clone to your first pull request written by the
factory and merged by you. It assumes you are comfortable with git, GitHub and a
terminal. Plan for about an hour the first time; most of it is one-off setup.

You will:

1. Run a free offline demo, so you see the factory work before you spend anything.
2. Create a GitHub App that lets the factory read issues and open pull requests.
3. Prepare your product repository: configuration, labels, protection, hidden tests.
4. Fill in two environment files.
5. Start the factory and its console, and run your first issue.

The commands work in bash, zsh and PowerShell unless a step says otherwise. Paths
such as `~/factory` are examples; use whatever folders you like.

---

## How it works, in one minute

You write a GitHub issue that says what must be true when the work is done, and the
acceptance tests that prove it. You keep those tests on a separate `holdout` branch,
which the factory's agents never see. Then you add the label `factory:ready`.

1. **Plan.** The planner agent turns the issue into a spec under `specs/`. If the
   issue is unclear, it asks in the issue and waits for your answer.
2. **Risk.** Code scores the spec. Above the threshold, the plan waits for you to add
   `factory:plan-approved`.
3. **Build.** The builder agent writes the code and its unit tests in its own branch,
   `agent/<issue>`. Your format, lint, typecheck and test commands must pass; the
   builder gets up to three repair rounds.
4. **Review.** A reviewer agent on a different vendor's model checks the change
   against the spec. A policy in code turns its findings into a verdict.
5. **Acceptance.** Your hidden acceptance tests run against the change.
6. **Pull request.** The factory opens a pull request labelled `factory:review`.
   You review it and merge it.

If anything stops a task, the factory labels the issue `factory:needs-human` and
explains why in a comment. Fix the cause and remove the label to continue.

---

## Step 1 · Install the tools

| You need | Why | Check |
| --- | --- | --- |
| Python 3.12 and [uv](https://docs.astral.sh/uv/) | Runs the orchestrator | `uv --version` |
| Node.js 20 or later | Runs the console and the Codex CLI | `node --version` |
| git | Branches and worktrees | `git --version` |
| [GitHub CLI](https://cli.github.com/), signed in | Labels, rulesets, merging | `gh auth status` |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | The planner and builder | `claude --version` |
| [Codex CLI](https://github.com/openai/codex) | The reviewer | `codex --version` |
| An Anthropic API key with credit | Planner and builder calls | |
| An OpenAI API key with credit | Reviewer calls | |

Install the Codex CLI with `npm i -g @openai/codex` if you do not have it.

The factory requires two different vendors for the builder and the reviewer, so a
model never reviews its own family's work. It refuses to start otherwise.

Then get the factory and its dependencies:

```bash
git clone https://github.com/<you>/tank-factory-v1.git ~/factory/tank-factory-v1
cd ~/factory/tank-factory-v1
uv sync
cd console && npm ci && cd ..
uv run factory --version
```

---

## Step 2 · Try it offline (free)

This seeds a small demo repository and runs one task with the **fake harness**, a
scripted stand-in for the agents. No keys, no GitHub, no cost.

```bash
uv run factory dev seed ~/factory/demo
uv run factory run-spec --repo ~/factory/demo --issue 1 --harness fake
uv run factory trace --task dev/demo#1 --data-dir ~/factory/demo/.factory-dev/data
```

You should see `pass · commit … · cost $0.31` and then every step of the task:
the builder's tool calls, the gates and the commit. The cost is the script's own
number; nothing was charged. The task id is `dev/<folder name>#<issue>`.

The seeded demo is also your **reference product repository**: you will copy its
configuration in Step 4.

---

## Step 3 · Create the GitHub App

The factory acts on GitHub as an App you own, with only the permissions it needs.

1. On GitHub: your profile picture → **Settings** → **Developer settings** →
   **GitHub Apps** → **New GitHub App**.
2. **GitHub App name**: anything unique, for example `tank-factory-<you>`.
3. **Homepage URL**: `http://localhost:3100`.
4. **Callback URLs**: add `http://localhost:3100/auth/callback/github` and
   `http://127.0.0.1:3100/auth/callback/github`. The console uses these to sign
   you in with GitHub.
5. Untick **Expire user authorization tokens**.
6. **Webhook**: untick **Active**. The factory polls GitHub every 60 seconds, so it
   needs no public address.
7. **Repository permissions**: **Contents**, **Issues** and **Pull requests** set
   to **Read and write**. Metadata becomes read-only by itself.
8. **Where can this GitHub App be installed?**: **Only on this account**.
9. Click **Create GitHub App**.

On the App's page, write down the **App ID** and the **Client ID**. Click
**Generate a new client secret** and copy it now; GitHub shows it once. Click
**Generate a private key**; save the downloaded `.pem` file somewhere outside any
repository, for example `~/factory/data/factory-app.private-key.pem`.

Finally, in the left menu click **Install App**, install it on your account,
choose **Only select repositories** and pick your product repository. The page you
land on ends in `/installations/<number>`: that number is the **installation ID**.

---

## Step 4 · Prepare your product repository

Your product repository is the codebase the factory works on. It needs a
configuration folder, an issue template, labels, branch protection and a
`holdout` branch for the acceptance tests.

### 4a · Copy the configuration from the demo

From the demo you seeded in Step 2, copy these into your repository and commit them
to `main`:

| From the demo | What it is |
| --- | --- |
| `.factory/roster.yaml` | Which harness and model each agent uses, with turn, cost and time caps |
| `.factory/quality.yaml` | Your format, lint, typecheck, test and full-suite commands |
| `.factory/risk.yaml` | The risk instrument: tiers, multipliers and the approval threshold |
| `.factory/review-policy.yaml` | How review findings become a verdict |
| `.factory/factory.yaml` | Concurrency, extra protected paths, the holdout branch |
| `.github/ISSUE_TEMPLATE/factory-task.yml` | The issue form the planner expects |
| `CODEOWNERS` | Who must approve changes; put your own login in it |

### 4b · Edit the commands for your stack

`.factory/quality.yaml` holds five commands as argument lists. The demo's commands
point at the factory's own Python environment; replace them with your project's.
The `test` command gets the spec's unit-test ids appended, so it must accept them.

For a Python project managed with uv:

```yaml
format: ["uv", "run", "ruff", "format", "--check", "."]
lint: ["uv", "run", "ruff", "check", "."]
typecheck: ["uv", "run", "mypy", "."]
test: ["uv", "run", "pytest", "-q"]
full_suite: ["uv", "run", "pytest", "-q"]
```

For a Node project:

```yaml
format: ["npx", "prettier", "--check", "."]
lint: ["npm", "run", "lint"]
typecheck: ["npx", "tsc", "--noEmit"]
test: ["npx", "vitest", "run"]
full_suite: ["npm", "test", "--", "--run"]
```

`factory run-spec` and `factory install` run a self-test that checks each command
really fails on broken code, using the known-failing fixture under
`.factory-dev/known_failing/`. Copy that folder from the demo too, and adapt it if
your stack is not Python.

### 4c · Set the holdout branch

Add this line to `.factory/factory.yaml`:

```yaml
holdout_branch: holdout
```

Without it the acceptance tests would live on `main`, where every agent can read them.

### 4d · Create the labels

```bash
REPO=<you>/<repo>
gh label create "factory:ready" -R $REPO --color 0e8a16 --description "Build this issue"
gh label create "factory:plan-review" -R $REPO --color fbca04 --description "The plan waits for approval"
gh label create "factory:plan-approved" -R $REPO --color 0e8a16 --description "A human approved the plan"
gh label create "factory:needs-human" -R $REPO --color d93f0b --description "Blocked: a human must act"
gh label create "factory:budget" -R $REPO --color d93f0b --description "Stopped at the budget"
gh label create "factory:review" -R $REPO --color 1d76db --description "A factory PR waits for review"
gh label create "risk:low" -R $REPO --color c2e0c6 --description "Low risk"
gh label create "risk:high" -R $REPO --color f9d0c4 --description "High risk: plan approval required"
gh label create "risk:governed" -R $REPO --color b60205 --description "Governed path: code owner approval"
```

In PowerShell, set the variable with `$REPO = "<you>/<repo>"` and write `$REPO`
the same way.

### 4e · Protect `main`

The factory checks that `main` requires a code owner's review before it goes live.
In your repository: **Settings** → **Rules** → **Rulesets** → **New branch
ruleset**. Target the default branch, add **Require a pull request before merging**
with **Require review from Code Owners**, set **Enforcement status** to **Active**
and save. On the free GitHub plan this works for public repositories; private ones
need GitHub Pro or a team plan. Then check it:

```bash
uv run factory protection --org <you> --repo <repo>
```

### 4f · Create the holdout branch

The holdout branch holds only `tests/acceptance/`. Create it once, without history:

```bash
git switch --orphan holdout
mkdir -p tests/acceptance
touch tests/__init__.py tests/acceptance/__init__.py
cp ~/factory/tank-factory-v1/docs/templates/holdout-conftest.py tests/acceptance/conftest.py
git add tests && git commit -m "test: the holdout"
git push -u origin holdout
git switch main
```

The `conftest.py` matters as soon as two issues run at once: it keeps issue 2's
acceptance tests from failing issue 1's change while issue 2's code does not exist
yet. Read its docstring for the two naming rules your test files follow.

---

## Step 5 · Write your first issue and its acceptance tests

Open a new issue with the **Factory task** template. Fill every section:

- **Outcome**: what is true when this is done, in a sentence or two.
- **Scope**: the files the change may touch, one per line.
- **Non-goals**: what it must not do.
- **Acceptance criteria**: numbered, each in the form
  `WHEN <condition> THE SYSTEM SHALL <behaviour>`. Name exact functions, arguments
  and results; the planner and builder work from this text only.
- **Holdout**: the ids of the acceptance tests, one per line, such as
  `tests/acceptance/test_1_slugify.py::test_ac1_basic`.

Then write those tests on the `holdout` branch, named `test_<issue>_<name>.py` with
`# holdout-requires: <your.module>` as their first line, and push. Do this **before**
the next step: the factory refuses a plan whose holdout tests are missing.

Keep the first issue small: one module and its unit tests.

---

## Step 6 · Fill in the environment files

Copy the two templates:

```bash
cp .env.example .env
cp console/.env.example console/.env
```

Both files are ignored by git. Generate three secrets, one command each:

```bash
uv run python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

In **`.env`** (the factory):

| Line | Value |
| --- | --- |
| `ANTHROPIC_API_KEY` | Your Anthropic key |
| `CODEX_API_KEY` | Your OpenAI key |
| `FACTORY_CLAUDE_BIN` | `claude`, or the full path to the Claude Code binary |
| `FACTORY_CLAUDE_BARE` | `1`, to bill the API key above |
| `FACTORY_GITHUB_APP_ID` | The App ID from Step 3 |
| `FACTORY_GITHUB_INSTALLATION_ID` | The installation ID from Step 3 |
| `FACTORY_GITHUB_PRIVATE_KEY_FILE` | The path to the `.pem` file |
| `FACTORY_DATA_DIR` | A folder for the trace and worktrees, for example `~/factory/data` |
| `FACTORY_SERVICE_TOKEN` | Secret 1 |

In **`console/.env`** (the console):

| Line | Value |
| --- | --- |
| `ORCHESTRATOR_URL` | `http://127.0.0.1:8000` |
| `FACTORY_SERVICE_TOKEN` | Secret 1 again, the same value |
| `AUTH_SECRET` | Secret 2 |
| `CONSOLE_SESSION_KEY` | Secret 3 |
| `AUTH_GITHUB_ID` | The Client ID from Step 3 |
| `AUTH_GITHUB_SECRET` | The client secret from Step 3 |
| `CONSOLE_DB_PATH` | A file in your data folder, for example `~/factory/data/console.db` |

Leave any line you are not using commented out. An empty value such as
`FACTORY_GITHUB_INSTALLATION_ID=` breaks every command, because the factory reads it
as a number. Check the file loads with `uv run factory --help`.

---

## Step 7 · Start the factory and the console

The factory works in its own clone of your repository, with a local copy of the
holdout branch:

```bash
git clone https://github.com/<you>/<repo>.git ~/factory/<repo>-clone
cd ~/factory/<repo>-clone
git config core.autocrlf false
git branch holdout origin/holdout
cd ~/factory/tank-factory-v1
```

`core.autocrlf false` keeps line endings exactly as committed, which matters on
Windows.

Start the factory in one terminal and leave it running:

```bash
uv run factory serve --repo-clone ~/factory/<repo>-clone --org <you> --repo <repo> \
  --harness roster --data-dir ~/factory/data --budget 6
```

In PowerShell, put the whole command on one line instead of using `\`.

`--budget 6` is the per-task budget in US dollars. The day's spending cap is the
concurrency times three times the budget: 3 × 3 × $6 = $54 by default.

Build and start the console in a second terminal:

```bash
cd console
npm run build
npx next start -p 3100
```

Open `http://localhost:3100` and sign in with GitHub. You should see your repository
name in the header and the Overview screen.

---

## Step 8 · Run your first issue

1. Add the label `factory:ready` to your issue. Within a minute the planner starts;
   the console's Overview shows it.
2. If the plan scores high risk, the issue gets `factory:plan-review` and a
   **Plan review** comment with the spec. Read it, then approve by adding the label
   `factory:plan-approved`, or with **Write factory:plan-approved** on the console's
   Approvals screen.
3. The builder, the gates, the reviewer and your acceptance tests run. Watch the
   task's live trail on the console's task page.
4. A pull request opens, labelled `factory:review`. Review it, approve it and merge
   it. The factory sees the merge and closes the task.

That is the whole loop. Label more issues the same way; by default three run at once.
An issue whose code depends on another issue should wait until that one has merged
before you label it.

---

## Everyday controls

| To | Do |
| --- | --- |
| Stop everything now | `uv run factory stop --reason "<why>"`, or the console's **Stop factory** |
| Start again | `uv run factory start --reason "<why>"` |
| Continue a blocked task | Fix what its comment names, then remove `factory:needs-human` |
| Answer the planner's questions | Reply in the issue, then remove `factory:needs-human` |
| Give one task more money | The console's **Raise budget** on the task page, once, up to double |
| See a task's full record | `uv run factory trace --task <you>/<repo>#<n> --data-dir ~/factory/data` |

---

## Troubleshooting

| What you see | What it means | What to do |
| --- | --- | --- |
| A task blocks with `timeout` and "Credit balance is too low" | Your model account ran out of credit | Add credit, then remove `factory:needs-human` |
| Every plan waits with `risk:high` | The risk instrument rates the change at or above 50 | Approve the plans, or raise `thresholds.high` in `.factory/risk.yaml` with a pull request |
| The planner asks where a class or module is | The issue relies on code that has not merged yet | Merge the issue it depends on first, then answer and remove the label |
| The full suite fails on another issue's acceptance tests | The holdout has tests for unmerged issues | Add the `conftest.py` from Step 4f and the `# holdout-requires:` first lines |
| "builder and reviewer share one vendor" | The roster puts both on the same vendor | Put the reviewer on Codex, or the builder on another vendor |
| `missing_holdout` | The issue names a holdout test the branch does not have | Push the test to `holdout`, then remove the label |
| The console shows no data | It cannot reach the factory | Check the factory is running and `ORCHESTRATOR_URL` matches its port |
| GitHub sign-in fails with a redirect error | The callback URL is not registered | Add the console's exact address under the App's callback URLs |
| Port 3000 or 3100 is busy | Another program uses it | Start the console on another port and add that callback URL |

---

## Going further

- **Containers.** With Docker, each task runs in a sealed container with no network
  except the model proxy: see `factory image` and `factory proxy` in
  `uv run factory --help`, and `deploy/`.
- **A VM install.** `uv run factory install --help` describes the one-command install
  on a Linux VM, with the systemd unit in `deploy/`.
- **The eval set.** `uv run factory eval --help`: replay merged issues to measure a
  configuration change before it ships.
- **The design.** `docs/tank-factory-v1-prd.md` says what the product must do and
  why; `docs/tank-factory-v1-trd.md` says how it is built.
