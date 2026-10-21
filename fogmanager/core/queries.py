"""Cross-source queries: categories/sequences/samples/status.

Implemented once against the Source protocol, exercised through api.Client,
and merely surfaced by the CLI. Completeness is uniform across sources: a
sequence is complete iff every index row's file exists on disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..errors import UnknownSampleError
from ..sources.base import Source
from . import catalog
from .catalog import CategoryIndex, SampleRow
from .model import (
    CatalogTotals,
    CategoryStatus,
    CategorySummary,
    Sample,
    SequenceSummary,
    SourceStatus,
    StatusReport,
)


def _require_category(source: Source, category: str) -> CategoryIndex:
    # Missing index files raise CatalogError from the reader; index-only
    # categories (zero samples) are valid and return empty summaries.
    return source.read_index(category)


def _index_summaries(source: Source, index: CategoryIndex) -> tuple[int, int, int, int, bool]:
    """(sequences, present, total, on-size-bytes, complete) for one index."""
    category_dir = source.category_dir(index.name)
    on_disk = catalog.scan_category_disk(category_dir)
    total = len(index.rows)
    present = sum(1 for row in index.rows if row.vdb_path in on_disk)
    size_bytes = sum(on_disk.values())
    seq_names = {row.seq_key for row in index.rows}
    complete_seqs = 0
    for name in seq_names:
        rows = [r for r in index.rows if r.seq_key == name]
        if all(r.vdb_path in on_disk for r in rows):
            complete_seqs += 1
    return len(seq_names), present, total, size_bytes, complete_seqs == len(seq_names)


def list_categories(sources: list[Source]) -> list[CategorySummary]:
    """Merged category summaries, tagged with their source."""
    out: list[CategorySummary] = []
    for source in sources:
        for name in source.list_categories():
            index = source.read_index(name)
            seqs, present, total, size_bytes, complete = _index_summaries(source, index)
            out.append(
                CategorySummary(
                    source=source.name,
                    kind=source.kind,
                    name=name,
                    category=index.category,
                    num_samples=index.num_samples,
                    sequences=seqs,
                    vdb_total=total,
                    vdb_present=present,
                    size_bytes=size_bytes,
                    complete=complete,
                )
            )
    return out


def category_totals(source: Source, category: str) -> CatalogTotals:
    index = _require_category(source, category)
    folders: list[str] = []
    for row in index.rows:
        folder = row.vdb_path.split("/", 1)[0]
        if folder not in folders:
            folders.append(folder)
    on_disk = catalog.scan_category_disk(source.category_dir(index.name))
    size = sum(on_disk.values())
    return CatalogTotals(
        source=source.name,
        category=index.category,
        num_samples=index.num_samples,
        sequences=sorted({row.seq_key for row in index.rows}),
        folders=folders,
        size_bytes=size,
    )


def list_sequences(source: Source, category: str) -> list[SequenceSummary]:
    index = _require_category(source, category)
    on_disk = catalog.scan_category_disk(source.category_dir(index.name))
    order: list[str] = []
    grouped: dict[str, list[SampleRow]] = {}
    folders: dict[str, set[str]] = {}
    for row in index.rows:
        if row.seq_key not in grouped:
            grouped[row.seq_key] = []
            order.append(row.seq_key)
        grouped[row.seq_key].append(row)
        folders.setdefault(row.seq_key, set()).add(row.vdb_path.split("/", 1)[0])
    out: list[SequenceSummary] = []
    for name in order:
        rows = grouped[name]
        present = sum(1 for r in rows if r.vdb_path in on_disk)
        out.append(
            SequenceSummary(
                source=source.name,
                category=index.name,
                name=name,
                num_samples=len(rows),
                vdb_present=present,
                vdb_total=len(rows),
                size_bytes=sum(on_disk[r.vdb_path] for r in rows if r.vdb_path in on_disk),
                complete=present == len(rows),
                folders=sorted(folders[name]),
            )
        )
    return out


def list_samples(
    source: Source,
    category: str,
    *,
    sequence: str | None = None,
    limit: int | None = None,
) -> list[Sample]:
    index = _require_category(source, category)
    on_disk = catalog.scan_category_disk(source.category_dir(index.name))
    out: list[Sample] = []
    for row in index.rows:
        if sequence is not None and row.seq_key != sequence:
            continue
        path = source.resolve_sample_path(category, row)
        out.append(
            Sample.from_row(
                row,
                source=source.name,
                category=category,
                present=row.vdb_path in on_disk,
                path=path,
            )
        )
        if limit is not None and len(out) >= limit:
            break
    return out


def resolve_sample_paths(
    source: Source, category: str, sample_id: str
) -> list[Sample]:
    """All samples in a category whose id matches (or whose vdb_path stem matches)."""
    index = _require_category(source, category)
    on_disk = catalog.scan_category_disk(source.category_dir(index.name))
    matches: list[SampleRow] = [
        row for row in index.rows if row.id == sample_id
    ]
    if not matches:
        # Fallback: match the on-disk file stem.
        matches = [
            row
            for row in index.rows
            if Path(row.vdb_path).stem == sample_id
            or row.vdb_path.split("/", 1)[1] == sample_id
        ]
        if not matches:
            raise UnknownSampleError(
                f"sample id {sample_id!r} not found in category {category!r} "
                f"(source {source.name!r})"
            )
    return [
        Sample.from_row(
            row,
            source=source.name,
            category=category,
            present=row.vdb_path in on_disk,
            path=source.resolve_sample_path(category, row),
        )
        for row in matches
    ]


def read_sample_meta(source: Source, category: str, sample_id: str) -> dict[str, Any]:
    samples = resolve_sample_paths(source, category, sample_id)
    meta_path = samples[0].meta_path
    if not meta_path:
        raise UnknownSampleError(
            f"sample {sample_id!r} has no per-sample meta JSON"
        )
    return catalog.read_sample_meta(source.category_dir(category), meta_path)


def source_status(source: Source, category: str | None = None) -> SourceStatus:
    if category is not None:
        # A category not present under this source contributes nothing (other
        # sources may still hold it).
        if category not in source.list_categories() and not source.category_dir(category).is_dir():
            return None
        names = [category]
    else:
        names = source.list_categories()
    entries: list[CategoryStatus] = []
    for name in names:
        entries.append(_category_status(source, name))
    status = SourceStatus(
        source=source.name,
        kind=source.kind,
        data_root=str(source.data_root),
        free_bytes=catalog_disk_free(source.data_root),
        categories=entries,
    )
    _attach_source_details(source, status)
    return status


def build_status(sources: list[Source], category: str | None = None) -> StatusReport:
    return StatusReport(
        sources=[
            status
            for source in sources
            if (status := source_status(source, category)) is not None
        ]
    )


def catalog_disk_free(data_root: Path) -> int:
    import shutil

    return shutil.disk_usage(Path(data_root)).free


def _category_status(source: Source, name: str) -> CategoryStatus:
    from ..errors import CatalogError

    category_dir = source.category_dir(name)
    on_disk = catalog.scan_category_disk(category_dir)
    try:
        index = source.read_index(name)
    except CatalogError:
        return CategoryStatus(
            name=name,
            index_present=False,
            num_samples=None,
            vdb_total=None,
            vdb_present=None,
            disk_walk_total=len(on_disk),
            disk_bytes=sum(on_disk.values()),
            sequences_complete=0,
            sequences_partial=0,
            sequences_absent=0,
        )
    grouped: dict[str, list[SampleRow]] = {}
    for row in index.rows:
        grouped.setdefault(row.seq_key, []).append(row)
    complete = partial = absent = 0
    for rows in grouped.values():
        missing = any(r.vdb_path not in on_disk for r in rows)
        any_present = any(r.vdb_path in on_disk for r in rows)
        if not missing:
            complete += 1
        elif any_present:
            partial += 1
        else:
            absent += 1
    present = sum(1 for r in index.rows if r.vdb_path in on_disk)
    return CategoryStatus(
        name=name,
        index_present=True,
        num_samples=index.num_samples,
        vdb_total=len(index.rows),
        vdb_present=present,
        disk_walk_total=len(on_disk),
        disk_bytes=sum(on_disk.values()),
        sequences_complete=complete,
        sequences_partial=partial,
        sequences_absent=absent,
    )


def _attach_source_details(source: Source, status: SourceStatus) -> None:
    """Optional vfxdb extras: pinned revision + cache usage; none for imports."""
    pinned = getattr(source, "pinned_revision", None)
    if callable(pinned):
        status.pinned_revision = pinned()
    cache_info = getattr(source, "cache_info", None)
    if callable(cache_info):
        try:
            status.cache_bytes = cache_info().bytes_used
        except Exception:  # noqa: BLE001 - cache inspection is best-effort
            status.cache_bytes = None