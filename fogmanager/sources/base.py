"""The Source abstraction.

Core queries call only this protocol; source adapters never import the CLI.
Every read command is implemented once against this interface in
core/queries.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.catalog import SampleRow, sample_disk_path

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..core.catalog import CategoryIndex


class Source(ABC):
    """A registered dataset source."""

    #: Registry name, e.g. "vfxdb" or "my-sims".
    name: str = ""
    #: One of "vfxdb_hf" | "local_import" (plus future kinds).
    kind: str = ""
    #: Whether this source can orchestrate downloads.
    can_download: bool = False
    #: Root directory holding <Category>/... for this source.
    data_root: Path

    @abstractmethod
    def list_categories(self) -> list[str]: ...

    @abstractmethod
    def read_index(self, category: str) -> "CategoryIndex": ...

    def category_dir(self, category: str) -> Path:
        return Path(self.data_root) / category

    def resolve_sample_path(self, category: str, row: SampleRow) -> Path:
        return sample_disk_path(self.category_dir(category), row)


class DownloadCapableSource(Source):
    """Sources that can orchestrate official downloads (vfxdb only today).

    ``run_official`` is the monkeypatch/testing seam and the single place the
    external tool is ever executed.
    """

    @abstractmethod
    def fetch_tool(self, *, revision: str | None = None, refresh: bool = False) -> Any: ...

    @abstractmethod
    def install_metadata(self, *, include_bad: bool = False, revision: str | None = None,
                         dry_run: bool = False) -> int: ...

    @abstractmethod
    def download(self, argv: list[str]) -> int: ...

    @abstractmethod
    def cache_info(self) -> Any: ...