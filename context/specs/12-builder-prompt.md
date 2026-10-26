# Unit 12: Builder prompt and prompt builder

## Goal

Ship the builder's guide and the only function that assembles a prompt:
a byte-stable system prompt, a user prompt in the PRD's prefix order (write
boundary and command names; spec; previous `VerifyOutput` on repair;
reviewer findings on revision), an allow-list that refuses anything else,
and a guide sha recorded on every agent start (TRD §8).

## Design

No UI. Visible result: the same inputs produce the same prompt bytes; the
assembled prompt for the green spec has its sections in order; a secret
pattern or a non-text input raises before any harness call.

## Implementation

### prompts/builder/system.md, prompts/builder/user.md

- The system guide states what the builder receives, the seven rules code
  enforces (boundary, test directories, no dependency changes, no git or
  network commands, test-first, no test gaming, true claims) and the
  `BuildOutput` contract. It contains no placeholders.
- The user template has `{{write_boundary}}`, `{{command_names}}`,
  `{{spec}}`, `{{previous_verify}}`, `{{reviewer_findings}}` in that order;
  the last two render empty when absent so the prefix stays stable.

### orchestrator/prompt_builder.py

- `Guide(system_template, user_template, source, sha)` and
  `load_guide(role, override_dir)`: factory defaults from `prompts/<role>/`
  (force-included in the wheel), per-file overrides from
  `.factory/prompts/<role>/`; a missing default raises `ConfigError`.
- `build_builder_prompt(guide, spec_text, writes, protected_paths,
  test_dirs, commands, focused_tests, previous_verify, reviewer_findings)
  -> PromptBundle(system_prompt, user_prompt, guide_sha, sections)`.
- `forbid_secrets(*texts)` rejects token and key shapes (`ghp_`, `ghs_`,
  `github_pat_`, `sk-ant-`, `sk-`, `Bearer `, `API_KEY=`); every string
  that enters a prompt passes through it; a non-string input raises
  `PromptError`.

### orchestrator/errors.py

- `PromptError` with code `prompt_invalid`.

## Dependencies

- None new.

## Verify when done

- [ ] `load_guide(builder)` returns the factory templates with a 64-hex sha
      that is stable across calls; an override directory replaces only the
      files it contains and the sha changes
- [ ] The system prompt equals the file byte for byte
- [ ] The user prompt lists the sections in order: write boundary, commands,
      spec; repair adds "Previous verification result" with the gate name;
      revision adds "Reviewer findings"; neither appears otherwise
- [ ] Focused test ids appear on the test command line
- [ ] Two calls with the same inputs return identical bytes
- [ ] A spec containing `ghp_` or `sk-ant-` raises `PromptError`; a dict or
      settings object passed as the spec raises `PromptError`
- [ ] The four quality commands pass
