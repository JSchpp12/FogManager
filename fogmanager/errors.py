"""Exception hierarchy — part of the stable public API (see api.py).

Every error raised intentionally by FogManager subclasses ``FogManagerError``
so callers can catch one base class. Never raise bare exceptions across the
public boundary.
"""

from __future__ import annotations

__all__ = [
    "FogManagerError",
    "ConfigError",
    "SourceNotFoundError",
    "UnknownCategoryError",
    "UnknownSampleError",
    "CatalogError",
    "ToolError",
    "OfficialToolError",
    "SpaceGuardError",
    "IndexExistsError",
    "DependencyError",
]


class FogManagerError(Exception):
    """Base class for every FogManager error."""


class ConfigError(FogManagerError):
    """Malformed, unsupported, or inconsistent configuration."""


class SourceNotFoundError(FogManagerError):
    """A requested source (or the default vfxdb source) is not registered."""


class UnknownCategoryError(FogManagerError):
    """A category name does not exist for the given source."""


class UnknownSampleError(FogManagerError):
    """A sample id does not resolve within the given category."""


class CatalogError(FogManagerError):
    """A category index or control file cannot be read or is malformed."""


class ToolError(FogManagerError):
    """The official downloader tool is missing, stale, or unequipped."""


class OfficialToolError(FogManagerError):
    """The official downloader ran and reported a failure."""


class SpaceGuardError(FogManagerError):
    """Estimated need exceeds the disk-space guard; rerun with --force to override."""


class IndexExistsError(FogManagerError):
    """An imported category already has an index; pass --re-index to rewrite."""


class DependencyError(FogManagerError):
    """A required third-party package is missing."""