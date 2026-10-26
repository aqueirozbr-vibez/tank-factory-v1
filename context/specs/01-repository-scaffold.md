# Unit 01: Repository scaffold

## Goal

Turn the empty `app/` folder into the factory repository: a git repository
with a `uv`-managed Python 3.12 project holding the `orchestrator` and `cli`
packages, the quality toolchain configured, the PRD's constants defined once,
a settings class, an error base, and a `factory --version` command. Nothing
else: no trace, no harness, no phases.

## Design

No UI. The visible result is a green run of the four quality commands and the
version string.

## Implementation

### Git

- `git init -b main` in `app/`; `.gitignore` for `.venv/`, caches, `*.db`,
  `dist/`, `node_modules/`, `.env`, OS files.
- Work on branch `feat/01-repository-scaffold`; fast-forward into `main`
  when the checklist passes.

### Project

- `pyproject.toml`: project `factory` version `0.1.0`, `requires-python
  >=3.12,<3.13`, dependencies `pydantic` and `pydantic-settings`, dev group
  `ruff`, `mypy`, `pytest`; hatchling build with packages `orchestrator` and
  `cli`; script `factory = cli.main:main`.
- `.python-version` = `3.12`.
- `[tool.ruff]` line length 100, target py312, lint select `E F I UP B SIM N
  RUF ANN`.
- `[tool.mypy]` strict, `warn_unreachable`, pydantic plugin, files
  `orchestrator`, `cli`, `tests`.
- `[tool.pytest.ini_options]` testpaths `tests`.

### orchestrator package

- `orchestrator/__init__.py` exporting `factory_version`.
- `orchestrator/version.py`: `factory_version()` from package metadata with
  a `0.0.0+local` fallback.
- `orchestrator/constants.py`: every PRD number from `code-standards.md`
  General, as `Final` constants with a comment naming the PRD section.
- `orchestrator/errors.py`: `FactoryError(code, message, detail)` and
  `ConfigError`, `TraceError`, `GateError`, `HarnessError` subclasses, each
  with a stable `code` string.
- `orchestrator/models/__init__.py` and `orchestrator/models/settings.py`:
  `Settings(BaseSettings)` with env prefix `FACTORY_`: `enabled` (maps to
  `FACTORY_ENABLED`, default `True`), `data_dir` (default
  `/var/lib/factory`), `log_level` (`INFO`), `timezone`
  (`America/Sao_Paulo`), derived `trace_db_path` and `console_db_path`.
  Extra environment keys are ignored, unknown fields are not accepted in
  code.

### cli package

- `cli/__init__.py`, `cli/main.py`: `main(argv) -> int` with argparse;
  `--version` prints `factory <version>` and returns 0; no subcommands yet;
  no arguments prints help and returns 2.

### Tests

- `tests/unit/test_scaffold.py`: version is semver-like and the CLI prints
  it; settings defaults and the `FACTORY_ENABLED=0` override; a handful of
  constants equal their PRD values; `FactoryError` carries its code.

## Dependencies

- `pydantic` (models everywhere from unit 02 on)
- `pydantic-settings` (environment settings)
- dev: `ruff`, `mypy`, `pytest`

## Verify when done

- [ ] `uv run ruff format --check .` passes
- [ ] `uv run ruff check .` passes
- [ ] `uv run mypy .` passes
- [ ] `uv run pytest` passes
- [ ] `uv run factory --version` prints `factory 0.1.0`
- [ ] `git log` shows one commit on `main` for this unit
- [ ] `context/progress-tracker.md` marks unit 01 complete
