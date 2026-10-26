# The PreToolUse hook bundle (PRD §Capability 3; TRD §7.3)

What the harness loads before every agent phase:

- `factory_pretooluse.py`: the hook. Claude Code runs it before `Bash`,
  `Write`, `Edit`, `MultiEdit` and `NotebookEdit`. It denies with exit 2 and
  the reason on stderr: `git push` and `git commit`, `gh`, `curl`, `wget`,
  `rm -rf`, and any write to a protected path (the set arrives in
  `FACTORY_PROTECTED_PATHS`, `;`-separated; the PRD defaults are built in).
  Standard library only; it fails closed when it cannot parse its input.
- `settings.json`: the settings document Claude Code receives through
  `--settings` (bare mode skips the auto-discovery of `.claude/settings.json`,
  so the bundle is passed explicitly). It names the hook and adds the
  permission deny rules as a second layer. This copy is the container form;
  `orchestrator.workspace.hooks.hook_settings` renders the host form.
- `factory.rules`: the Codex counterpart. Codex has no hooks; the orchestrator
  installs this execpolicy file into `$CODEX_HOME/rules/` and runs Codex with
  `--sandbox workspace-write` (network off) or `read-only` for the reviewer.

In a task container the directory is mounted read-only at
`/opt/factory/hooks`; on the host the orchestrator uses this directory. Before
an agent phase starts, the orchestrator runs the hook once with a `git push`
and refuses to continue unless it denies. Hook denials appear in the trace as
`tool_call` rows with `denied: true`; the post-run diff remains the
enforcement for anything a hook cannot see.

Try it:

```
echo '{"tool_name":"Bash","tool_input":{"command":"git push origin main"},"cwd":"/work"}' \
  | python3 deploy/hooks/factory_pretooluse.py; echo "exit $?"
```
