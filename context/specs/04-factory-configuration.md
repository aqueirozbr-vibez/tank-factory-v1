# Unit 04: Factory configuration models and loader

## Goal

Read a product repository's `.factory/` directory into typed configuration
(roster, quality commands, protected paths, both instruments as text),
refuse anything the PRD forbids before it reaches the database, compute a
deterministic `config_sha`, and ship the default `.factory/` as a fixture.

## Design

No UI. Visible result: the default fixture loads, its sha is stable, and a
roster with one vendor for builder and reviewer is refused with a
`ConfigError` that names the rule.

## Implementation

### `.factory/` layout (product repository)

- `roster.yaml`: `planner`, `builder`, `reviewer`, each with `harness`
  (`claude_code`, `codex`, `fake`), `model` (pinned id), optional `vendor`
  (derived from the harness; required for `fake`), `thinking`, `tools`,
  `writes`, `max_turns`, `phase_cap_usd`, `timeout_s`.
- `quality.yaml`: argv lists for `format`, `lint`, `typecheck`, `test`
  (focused test ids are appended) and `full_suite`.
- `factory.yaml` (optional): `concurrency_n` default, extra
  `protected_paths` (additions only), `dependency_manifests`.
- `risk.yaml`, `review-policy.yaml`: the instruments, kept as text here and
  parsed by units 24 and 28.
- `prompts/<role>/system.md|user.md` (optional overrides, read by unit 12).

### orchestrator/models/config.py

- `RoleConfig`, `Roster`, `QualityCommands`, `FactoryConfig` as pydantic
  models with defaults from `constants.py`.
- Roster rules: builder and reviewer vendors differ; the reviewer writes
  nothing and uses only `Read`, `Glob`, `Grep`; the planner writes only
  `specs/`; `fake` names its vendor. Violations raise `ConfigError`.
- `FactoryConfig.vendors` and `config_version_row()` returning the kwargs
  for `TraceWriter.load_config_version`.

### orchestrator/config/loader.py

- `load_factory_config(root, *, main_commit="local")`: required files
  named in the error when missing; YAML parsed with `yaml.safe_load`;
  protected paths are the PRD set plus lockfiles plus any additions;
  `config_sha` = SHA-256 over every file under the directory, sorted by
  POSIX relative path, each as `path NUL size NUL bytes`.

### fixtures/factory_config/

- The shipped default: planner and builder on Claude Code (Anthropic),
  reviewer on Codex (OpenAI); Python quality commands as the template; the
  PRD's risk and review instruments as YAML.

## Dependencies

- `pyyaml` (YAML configuration); dev: `types-pyyaml`

## Verify when done

- [ ] The default fixture loads; `config_sha` is 64 hex characters and
      identical across two loads
- [ ] Changing one byte in any fixture file changes `config_sha`
- [ ] A roster with the same vendor for builder and reviewer raises
      `ConfigError` mentioning "vendor"
- [ ] A reviewer with `writes: ["src/"]` or tool `Bash` is refused
- [ ] A planner with `writes: ["src/"]` is refused
- [ ] `fake` without `vendor` is refused; with `vendor` it loads
- [ ] A missing `roster.yaml` raises `ConfigError` naming the file
- [ ] Protected paths always include the PRD five and the lockfiles, and
      additions are kept
- [ ] `config_version_row()` inserts through `TraceWriter.load_config_version`
- [ ] The four quality commands pass
