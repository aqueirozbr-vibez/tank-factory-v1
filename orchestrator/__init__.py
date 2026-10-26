"""Tank Factory v1: the orchestrator package.

Code owns the control flow; agents own one bounded phase each (PRD §Summary).
"""

from orchestrator.version import factory_version

__all__ = ["factory_version"]
