# Unit 38: PreToolUse hook bundle

## Goal

A `PreToolUse` hook denies `git push`, `gh`, `rm -rf`, `curl`, `wget` and
any write to a protected path before the tool runs (exit 2 with the
reason); Codex gets the equivalent sandbox flags; denials are visible as
`tool_call` rows (PRD §Capability 3 · Requirements; TRD §7.3, §11 prompt
injection row). Hooks and the post-run diff are the enforcement;
`CLAUDE.md` is advice.

## Design

Terminal only. Claude Code runs in bare mode, which skips the auto-discovery
of `.claude/settings.json` (CLI reference, 2026-10-24), so the bundle travels
as an explicit `--settings` document naming the hook script and the
permission deny rules. The bundle directory is `deploy/hooks/` on the host
and a read-only mount at `/opt/factory/hooks` in a task container; the
agent cannot change it in either mode. Before an agent phase the
orchestrator runs the hook once with a `git push` and refuses to continue
unless it denies (fail closed, like the quality self-test). `git commit` is
denied as well because the PRD states the agent never runs it.

## Implementation

### deploy/hooks/

- `factory_pretooluse.py`: standard-library hook. Bash commands are
  tokenised per line (`shlex`, punctuation aware), split on operators,
  unwrapped from `sudo`/`env`/`xargs`/`bash -c` and the like, then checked:
  `curl`/`wget`/`gh` always; `git push`/`git commit` (options before the
  subcommand skipped); `rm` with recursive and force; mutating commands,
  `sed -i`, `tee` and redirections naming a protected path; git
  tree-rewriting subcommands naming one. File tools: a path outside the
  workspace or on a protected path. The protected set comes from
  `FACTORY_PROTECTED_PATHS` (`;`-separated), defaulting to the PRD set plus
  lockfiles; matching mirrors `permissions.matches`. Unparsable input denies.
- `settings.json`: the shipped fragment (container form); `factory.rules`:
  the Codex execpolicy counterpart; `README.md`.

### orchestrator/workspace/hooks.py

- `hook_settings(hooks_dir, python)`, `settings_json`, `DENY_RULES`,
  `hook_command`, `codex_rules`, `install_codex_rules(codex_home)`,
  `agent_env_with_hooks(env, protected)`, `verify_hook(runner, ...)`,
  `HookError`, `default_hooks_dir`.

### Harness and phases

- `HarnessRequest.hooks_dir` and `hook_python`; `ToolCall.denied`.
- Claude Code: `--settings <json>` when `hooks_dir` is set; a tool result
  starting with `PreToolUse:<tool> hook error` marks the call denied.
- Codex: `-c sandbox_workspace_write.network_access=false` always.
- Fake harness: with `hooks_dir` set it runs the real hook for every
  scripted `Bash`/file tool call through its runner (inside the container
  in container mode) and emits the denial as the real stream would.
- Builder and planner phases pass the bundle and the protected set; the
  builder counts `hook_denials` on `agent_end`; the sink writes `denied`.
- Container: fourth mount `hooks_dir:/opt/factory/hooks:ro`; `CODEX_HOME`
  rewritten to the container home; the runner verifies the hook after
  the container starts; the service verifies the host hook and installs
  the Codex rules at start.

## Dependencies

- Unit 36. No new package. The real stream shape of a hook denial is
  hand-authored from the hooks reference and is replaced by the phase 3
  gate recording.

## Verify when done

- [ ] Each command on the deny list, including wrapped and chained forms,
      is refused by the hook with a reason; ordinary builder commands pass
- [ ] A file tool write to a protected or outside-workspace path is refused;
      the hook's matcher agrees with the gate's on generated paths
- [ ] The shipped `settings.json` and `factory.rules` equal the rendered
      forms; Claude Code's argv carries `--settings`; Codex's argv carries
      the network flag
- [ ] A fake-harness build replay issuing the denied commands records them
      as `tool_call` rows with `denied: true` and finishes green; a
      protected write in the effects is still blocked by the post-run diff
- [ ] `verify_hook` passes on the real script and fails on a missing one;
      opt-in on Docker, the hook denies inside the container
- [ ] The four quality commands pass
