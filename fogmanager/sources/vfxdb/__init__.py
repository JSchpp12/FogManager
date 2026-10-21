"""VfxDB (Hugging Face) source: the only download-capable source.

Exposes the adapter and the verified upstream facts (category table, revision
constants) that core and the CLI may reference indirectly through the source
registry.
"""

from .official import OFFICIAL_CATEGORIES, DEFAULT_REVISION, REPO_ID
from .source import CacheInfo, VfxdbSource

__all__ = [
    "OFFICIAL_CATEGORIES",
    "DEFAULT_REVISION",
    "REPO_ID",
    "CacheInfo",
    "VfxdbSource",
]