"""Factory version, read from the installed package metadata.

The installation pins a git tag; the package version follows that tag
(PRD §Architecture · Factory repository and installer).
"""

from importlib import metadata

_FALLBACK = "0.0.0+local"


def factory_version() -> str:
    """Return the installed factory version, or a local fallback."""
    try:
        return metadata.version("factory")
    except metadata.PackageNotFoundError:
        return _FALLBACK
