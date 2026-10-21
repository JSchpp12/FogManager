"""Serializable result types shared by core queries and the public API.

These are the *stable API surface*: every public query returns these
dataclasses, each with ``.to_dict()``; the CLI's ``--json`` output is exactly
those dicts. Defined here (core) so queries can build them without importing
api.py — api.py re-exports them as the contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .catalog import SampleRow


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_dict") and not isinstance(value, dict):
        return value.to_dict()
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


class ToDict:
    def to_dict(self) -> dict[str, Any]:
        return {k: _to_jsonable(v) for k, v in self.__dict__.items() if not k.startswith("_")}


@dataclass
class SourceInfo(ToDict):
    name: str
    kind: str
    can_download: bool
    data_root: str | None
    usable: bool = True
    detail: str | None = None


@dataclass
class CategorySummary(ToDict):
    source: str
    kind: str
    name: str
    category: str
    num_samples: int
    sequences: int
    vdb_total: int
    vdb_present: int
    size_bytes: int
    complete: bool


@dataclass
class SequenceSummary(ToDict):
    source: str
    category: str
    name: str
    num_samples: int
    vdb_present: int
    vdb_total: int
    size_bytes: int
    complete: bool
    folders: list[str] = field(default_factory=list)


@dataclass
class Sample(ToDict):
    source: str
    category: str
    id: str | None
    base_id: str | None
    seq: Any
    folder: str | None
    vdb_path: str
    meta_path: str | None
    size_bytes: int | None
    mtime: Any
    present: bool
    path: str

    @classmethod
    def from_row(cls, row: SampleRow, *, source: str, category: str, present: bool, path: Path) -> "Sample":
        return cls(
            source=source,
            category=category,
            id=row.id,
            base_id=row.base_id,
            seq=row.seq,
            folder=row.folder,
            vdb_path=row.vdb_path,
            meta_path=row.meta_path,
            size_bytes=row.size_bytes,
            mtime=row.mtime,
            present=present,
            path=str(path),
        )


@dataclass
class CategoryStatus(ToDict):
    name: str
    index_present: bool
    num_samples: int | None
    vdb_total: int | None
    vdb_present: int | None
    disk_walk_total: int
    disk_bytes: int
    sequences_complete: int
    sequences_partial: int
    sequences_absent: int


@dataclass
class SourceStatus(ToDict):
    source: str
    kind: str
    data_root: str
    free_bytes: int
    pinned_revision: str | None = None
    cache_bytes: int | None = None
    categories: list[CategoryStatus] = field(default_factory=list)

    def totals(self) -> tuple[int, int, int, int]:
        """(complete, partial, absent sequences, present vdb files)."""
        complete = sum(c.sequences_complete for c in self.categories)
        partial = sum(c.sequences_partial for c in self.categories)
        absent = sum(c.sequences_absent for c in self.categories)
        present = sum(c.vdb_present or 0 for c in self.categories)
        return complete, partial, absent, present


@dataclass
class StatusReport(ToDict):
    sources: list[SourceStatus] = field(default_factory=list)

    def find(self, name: str) -> SourceStatus | None:
        for entry in self.sources:
            if entry.source == name:
                return entry
        return None


@dataclass
class CatalogTotals(ToDict):
    """Rollup of an entire category index (used by inspect/list-categories)."""

    source: str
    category: str
    num_samples: int
    sequences: list[str]
    folders: list[str]
    size_bytes: int
    bad_samples: int | None = None


@dataclass
class SelectionTar(ToDict):
    """One selected tar archive in a download estimate."""

    source: str
    category: str
    folder: str
    samples: int
    bad_samples: int
    size_bytes: int


@dataclass
class DownloadEstimate(ToDict):
    """Result of a dry-run: what would be downloaded, and is there room."""

    source: str
    label: str
    revision: str
    data_root: str
    cache_dir: str
    tar_count: int
    available_tars: int
    normal_samples: int
    bad_samples: int
    extracted_bytes: int
    remote_known: bool
    remote_bytes: int
    free_bytes: int
    needed_bytes: int
    ok: bool
    warning: str | None = None
    command: str | None = None
    per_category: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ImportResult(ToDict):
    source: str
    root: str
    index_dir: str | None
    per_category: list[dict[str, Any]]
    total_samples: int
    total_bytes: int
    skipped_files: int