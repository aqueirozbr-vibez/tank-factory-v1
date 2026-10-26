"""``factory authz``: regenerate or check the authorisation modules (TRD §6.2)."""

from cli.common import say, warn
from orchestrator.authz.generate import check, generate
from orchestrator.errors import ConfigError


def authz(*, check_only: bool = False) -> int:
    try:
        if check_only:
            stale = check()
            if stale:
                warn("stale generated files: " + ", ".join(stale) + " (run `factory authz`)")
                return 1
            say("authz.py and authz.ts match authz.yaml")
            return 0
        table = generate()
        say(
            f"generated authz.py, authz.ts and authz.cases.json from authz.yaml"
            f" ({len(table.rules)} rows, {len(table.cases)} cases, sha {table.sha256[:12]})"
        )
        return 0
    except ConfigError as exc:
        warn(f"authz.yaml refused: {exc}")
        return 2


__all__ = ["authz"]
