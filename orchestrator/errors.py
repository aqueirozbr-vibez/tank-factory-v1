"""Domain exceptions with stable codes (code-standards.md · Python).

The API maps these to ``{code, message, detail?, traceId}``. Messages are
declarative and name the fix.
"""

from collections.abc import Mapping


class FactoryError(Exception):
    """Base class for every factory error."""

    code: str = "factory_error"

    def __init__(self, message: str, *, detail: Mapping[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, object] = dict(detail) if detail else {}

    def __str__(self) -> str:
        return self.message


class ConfigError(FactoryError):
    """A `.factory/` configuration or roster that cannot be loaded."""

    code = "config_invalid"


class TraceError(FactoryError):
    """The trace database refused a write or failed verification."""

    code = "trace_error"


class GateError(FactoryError):
    """A gate could not run (not a gate failure, which is a result)."""

    code = "gate_error"


class HarnessError(FactoryError):
    """A harness subprocess could not be started, parsed or stopped."""

    code = "harness_error"


class PromptError(FactoryError):
    """A prompt input outside the allow-list, or a credential-shaped value."""

    code = "prompt_invalid"


class FactoryDisabledError(FactoryError):
    """The kill-switch is 0 (or unreadable): no phase starts (PRD §Capability 5)."""

    code = "factory_disabled"
