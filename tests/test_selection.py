"""Selection estimator vs hand-computed official plans."""

from __future__ import annotations

import unittest

from fogmanager.core.catalog import CategoryIndex, SampleRow
from fogmanager.errors import UnknownCategoryError
from fogmanager.sources.vfxdb.selection import (
    all_archives,
    balanced,
    build_archives,
    select_tars,
)


def row(folder: str, name: str, size: int = 100, bad: bool = False) -> SampleRow:
    return SampleRow(
        id=name, base_id=name.split("__")[0], seq=None, folder=folder,
        vdb_path=f"{folder}/{name}.vdb", meta_path=None, size_bytes=size, mtime=None,
        row_index=0,
    )


def index(category: str, rows: list[SampleRow]) -> CategoryIndex:
    return CategoryIndex(name=category, category=category, num_samples=len(rows),
                         rows=rows, path=None)


def cat(name: str, folders: dict[str, int]) -> CategoryIndex:
    """Category with per-folder tar sizes in first-appearance order."""
    rows = []
    for folder, count in folders.items():
        rows.extend(row(folder, f"s_{folder}_{i}") for i in range(count))
    return index(name, rows)


IO_BAD = {"C/t2/x.vdb"}  # one bad member in category C tar t2


class SelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.indexes = {
            "A": cat("A", {"t0": 3, "t1": 2}),
            "B": cat("B", {"t0": 1}),
            "C": cat("C", {"t2": 2, "t9": 1}),  # t2 first-appearance order
        }
        self.total = 5  # A:2 + B:1 + C:2

    def test_first_appearance_order_and_key(self) -> None:
        archives = build_archives(self.indexes["C"])
        self.assertEqual([a.folder for a in archives], ["t2", "t9"])
        self.assertEqual(archives[0].rows[0].vdb_path, "t2/s_t2_0.vdb")

    def test_all_archives_sorted_by_category(self) -> None:
        order = [(a.category, a.folder) for a in all_archives(self.indexes)]
        self.assertEqual(
            order, [("A", "t0"), ("A", "t1"), ("B", "t0"), ("C", "t2"), ("C", "t9")]
        )

    def test_smoke_first_two_tars_per_category(self) -> None:
        plan = select_tars(self.indexes, preset="smoke")
        self.assertEqual(
            [(a.category, a.folder) for a in plan.tars],
            [("A", "t0"), ("A", "t1"), ("B", "t0"), ("C", "t2"), ("C", "t9")],
        )
        # B has only one tar: smoke takes what exists ([:2]).

    def test_medium_ceil_total_five_round_robin(self) -> None:
        # total=5 -> ceil(5/5)=1 tar, round robin over sorted categories.
        plan = select_tars(self.indexes, preset="medium")
        self.assertEqual([(a.category, a.folder) for a in plan.tars], [("A", "t0")])

        indexes = {
            "A": cat("A", {"t0": 1}),
            "B": cat("B", {"t0": 1}),
            "C": cat("C", {"t0": 1}),
            "D": cat("D", {"t0": 1}),
            "E": cat("E", {"t0": 1}),
            "F": cat("F", {"t0": 1}),
        }
        # total=6 -> ceil(6/5)=2: A then B.
        plan = select_tars(indexes, preset="medium")
        self.assertEqual([a.category for a in plan.tars], ["A", "B"])

    def test_percentage_ceiling(self) -> None:
        # 5 tars, 50% -> ceil(2.5)=3: round robin A, B, C.
        plan = select_tars(self.indexes, percentage="50")
        self.assertEqual([a.category for a in plan.tars], ["A", "B", "C"])
        # 100% short-circuits to all.
        plan = select_tars(self.indexes, percentage="100")
        self.assertEqual(plan.tar_count, 5)

    def test_max_samples_whole_tar_rounding(self) -> None:
        # A: t0 holds 3 normal samples -> target 2 is rounded up to whole tars.
        plan = select_tars(self.indexes, categories=["A"], max_samples=2)
        self.assertEqual([a.folder for a in plan.tars], ["t0"])
        self.assertEqual(plan.normal_total(None), 3)

        # target 3 == exactly t0; target 4 pulls in t1 as well.
        plan = select_tars(self.indexes, categories=["A"], max_samples=4)
        self.assertEqual([a.folder for a in plan.tars], ["t0", "t1"])
        self.assertEqual(plan.normal_total(None), 5)

    def test_max_samples_excludes_io_bad_from_quota(self) -> None:
        indexes = {"C": cat("C", {"t2": 2})}
        indexes["C"].rows.append(row("t2", "x"))  # the io-bad member
        # t2 = 2 normal + 1 bad; target 2 is satisfied inside one tar.
        plan = select_tars(indexes, IO_BAD, categories=["C"], max_samples=2)
        self.assertEqual([a.folder for a in plan.tars], ["t2"])
        self.assertEqual(plan.normal_total(IO_BAD), 2)
        self.assertEqual(plan.bad_total(IO_BAD), 1)
        # Target 3 pulls the next tar if there were one; with only t2, the tar
        # as a whole is taken once exhausted.
        plan = select_tars(indexes, IO_BAD, categories=["C"], max_samples=3)
        self.assertEqual([a.folder for a in plan.tars], ["t2"])

    def test_bad_rows_ride_along_in_extracted_bytes(self) -> None:
        indexes = {"C": cat("C", {"t2": 2})}
        indexes["C"].rows.append(row("t2", "x", size=100))
        plan = select_tars(indexes, IO_BAD, categories=["C"], max_samples=1)
        self.assertEqual(plan.extracted_bytes(), 300)  # bad member bytes included

    def test_extracted_bytes(self) -> None:
        plan = select_tars(self.indexes, categories=["A"], max_samples=1)
        self.assertEqual(plan.extracted_bytes(), 300)  # 3 rows x 100

    def test_unknown_category(self) -> None:
        with self.assertRaises(UnknownCategoryError):
            select_tars(self.indexes, categories=["Nope"], max_samples=1)

    def test_metadata_only(self) -> None:
        plan = select_tars(self.indexes)
        self.assertEqual(plan.tar_count, 0)
        self.assertEqual(plan.label, "metadata-only")

    def test_invalid_arguments(self) -> None:
        with self.assertRaises(ValueError):
            select_tars(self.indexes, categories=["A"], max_samples=None)
        with self.assertRaises(ValueError):
            select_tars(self.indexes, categories=["A"], max_samples=0)


if __name__ == "__main__":
    unittest.main()