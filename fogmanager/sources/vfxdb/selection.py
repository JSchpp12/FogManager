"""Tar-selection ESTIMATOR — replicates the official tool's documented plan.

This mirrors ``build_plan()`` in the official downloader (verified against its
source at revision 8b2abbf):

- Tar key is ``vdb_path``'s first component (the folder on disk); archives are
  ordered by first appearance of that folder in the index's rows.
- ``smoke``: first 2 tars of every category (categories sorted).
- ``medium``: ``ceil(total_tars / 5)`` tars, round-robin over sorted categories.
- ``full``: every tar.
- ``--percentage P``: ``ceil(total_tars * P / 100)`` tars, round-robin; P=100 short-circuits to all.
- ``--category/--max-samples N``: per category (sorted), walk archives in
  first-appearance order, accumulating *normal* (non-IO-bad) samples, stopping
  once cumulative normal count >= N (whole-tar upward rounding). N is applied
  independently to each --category.
- ``--include-bad`` never changes selection; bad rows ride along in their tars.

Only ever an estimate: the official tool's own printed plan is authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal
from typing import Any

from ...core.catalog import CategoryIndex, SampleRow, sample_key
from ...errors import UnknownCategoryError

SMOKE_TARS_PER_CATEGORY = 2


@dataclass
class SelectedTar:
    category: str
    folder: str
    rows: list[SampleRow] = field(default_factory=list)

    @property
    def all_samples(self) -> int:
        return len(self.rows)

    def normal_samples(self, io_bad: set[str] | None) -> int:
        if not io_bad:
            return self.all_samples
        return sum(1 for row in self.rows if sample_key(self.category, row) not in io_bad)


@dataclass
class SelectionPlan:
    label: str
    tars: list[SelectedTar] = field(default_factory=list)
    available_tars: int = 0
    requested_max: int | None = None

    @property
    def tar_count(self) -> int:
        return len(self.tars)

    @property
    def all_rows(self) -> list[SampleRow]:
        return [row for tar in self.tars for row in tar.rows]

    def normal_total(self, io_bad: set[str] | None) -> int:
        return sum(tar.normal_samples(io_bad) for tar in self.tars)

    def bad_total(self, io_bad: set[str] | None) -> int:
        return self.sum_rows() - self.normal_total(io_bad)

    def sum_rows(self) -> int:
        return sum(tar.all_samples for tar in self.tars)

    def extracted_bytes(self) -> int:
        return sum(row.size_bytes or 0 for row in self.all_rows)

    def per_category(self, io_bad: set[str] | None) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for tar in self.tars:
            entry = out.setdefault(
                tar.category,
                {"category": tar.category, "tars": 0, "all_samples": 0, "normal_samples": 0,
                 "extracted_bytes": 0},
            )
            entry["tars"] += 1
            entry["all_samples"] += tar.all_samples
            entry["normal_samples"] += tar.normal_samples(io_bad)
            entry["extracted_bytes"] += sum(r.size_bytes or 0 for r in tar.rows)
        return out


def build_archives(index: CategoryIndex) -> list[SelectedTar]:
    """Group rows into tars by vdb_path's first part, in first-appearance order."""
    by_folder: dict[str, SelectedTar] = {}
    order: list[str] = []
    for row in index.rows:
        folder = row.vdb_path.split("/", 1)[0]
        tar = by_folder.get(folder)
        if tar is None:
            tar = SelectedTar(category=index.category, folder=folder)
            by_folder[folder] = tar
            order.append(folder)
        tar.rows.append(row)
    return [by_folder[folder] for folder in order]


def all_archives(indexes: dict[str, CategoryIndex]) -> list[SelectedTar]:
    """Official ordering: categories sorted alphabetically, tars in category order."""
    return [
        tar
        for name in sorted(indexes)
        for tar in build_archives(indexes[name])
    ]


def balanced(indexes: dict[str, CategoryIndex], target: int) -> list[SelectedTar]:
    """Round-robin over sorted categories, like the official balanced_archives."""
    if target <= 0:
        return []
    names = sorted(indexes)
    positions = {name: 0 for name in names}
    per_category = {name: build_archives(indexes[name]) for name in names}
    selected: list[SelectedTar] = []
    while len(selected) < target:
        progressed = False
        for name in names:
            position = positions[name]
            archives = per_category[name]
            if position >= len(archives):
                continue
            selected.append(archives[position])
            positions[name] = position + 1
            progressed = True
            if len(selected) == target:
                break
        if not progressed:
            break
    return selected


def _require_categories(indexes: dict[str, CategoryIndex], requested: list[str]) -> None:
    unknown = [name for name in requested if name not in indexes]
    if unknown:
        raise UnknownCategoryError(
            f"unknown categories: {unknown}; available: {sorted(indexes)}"
        )


def select_tars(
    indexes: dict[str, CategoryIndex],
    io_bad: set[str] | None = None,
    *,
    preset: str | None = None,
    percentage: str | None = None,
    categories: list[str] | tuple[str, ...] = (),
    max_samples: int | None = None,
) -> SelectionPlan:
    """Estimate the official selection. Raises UnknownCategoryError as needed."""
    total = len(all_archives(indexes))

    if preset is not None:
        if preset == "smoke":
            selected = [
                tar
                for name in sorted(indexes)
                for tar in build_archives(indexes[name])[:SMOKE_TARS_PER_CATEGORY]
            ]
            return SelectionPlan(f"preset {preset}", selected, total)
        if preset == "medium":
            target = (total + 4) // 5  # ceil(total/5), as the official tool computes
            return SelectionPlan(f"preset {preset}", balanced(indexes, target), total)
        if preset == "full":
            return SelectionPlan(f"preset {preset}", all_archives(indexes), total)
        raise ValueError(f"unknown preset {preset!r}")

    if percentage is not None:
        pct = Decimal(str(percentage))
        target = int((Decimal(total) * pct / Decimal(100)).to_integral_value(rounding=ROUND_CEILING))
        if target == total:
            selected = all_archives(indexes)
        else:
            selected = balanced(indexes, target)
        return SelectionPlan(f"all-category {percentage}%", selected, total)

    if categories or max_samples is not None:
        requested = list(categories)
        _require_categories(indexes, requested)
        if max_samples is None:
            raise ValueError("--max-samples is required with --category")
        if max_samples <= 0:
            raise ValueError("--max-samples must be positive")
        selected: list[SelectedTar] = []
        for name in sorted(requested):
            cumulative = 0
            for tar in build_archives(indexes[name]):
                selected.append(tar)
                cumulative += tar.normal_samples(io_bad)
                if cumulative >= max_samples:
                    break
        return SelectionPlan(
            f"category max-samples={max_samples}", selected, total, requested_max=max_samples
        )

    # Metadata-only: nothing selected.
    return SelectionPlan("metadata-only", [], total)