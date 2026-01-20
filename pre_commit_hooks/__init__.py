"""Pre-commit hooks for automated file organization and repository management."""

from __future__ import annotations

__version__ = "0.0.0+unknown"

try:
    from pre_commit_hooks._version import __version__ as _version
    __version__ = _version
except ImportError:
    pass

__all__ = ["__version__"]
