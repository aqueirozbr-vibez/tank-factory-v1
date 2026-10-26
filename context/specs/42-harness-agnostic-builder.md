# Unit 42: Harness-agnostic builder

## Goal

The roster names a harness and a model per role; the same workflow runs the
builder on Claude Code or Codex with no code change; the orchestrator
refuses to start when builder and reviewer share a vendor; every agent
phase row carries the harness version (PRD §Capability 3 · Harness
adapter, §Capability 4 · two vendors; TRD §2.0, §11 "a harness binary
whose version cannot be read refuses to run").

## Design

Terminal only. The adapter contract (unit 06, 26) already hides the two
CLIs behind one call; this unit closes the remaining gaps and proves the
claim. A hand-authored Codex build stream joins the Claude one so the build
slice runs end to end on both adapters with recorded output (an effectful
replay writes the files the stream claims). At service start the roster's
harnesses are probed once and their versions logged; a binary that cannot
report a version stops the start. A one-vendor roster fails at config load
and `factory serve` reports it as a configuration error. In container mode
the Codex execpolicy rules file is written into the container's `CODEX_HOME`
after the hook check, so a Codex role inside a container gets the same
deny list as on the host.

## Implementation

- `fixtures/streams/codex_build_green.jsonl`: the Codex builder stream.
- `orchestrator/harness/registry.py`: `probe_harness_versions`.
- `orchestrator/service.py`: probe at start in roster mode and a `log`
  event with the roster and versions.
- `orchestrator/workspace/hooks.py`: `install_codex_rules_in(runner)`;
  the runner calls it after the hook check when any role runs on Codex;
  `constants.CONTAINER_CODEX_HOME`.
- `cli/serve.py`: `FactoryError` at start is reported, exit 2.

## Dependencies

- Units 26 and 38. No new package.

## Verify when done

- [ ] The build slice passes on each adapter from recorded streams with
      identical phases and gates and the harness and version on the phase row
- [ ] A roster with Codex for both builder and reviewer is refused at load;
      `factory serve` exits 2 with the message
- [ ] The start probes every roster harness and refuses an unreadable one
- [ ] A container start writes the Codex rules into the container home
- [ ] The four quality commands pass
