"""Catalog readers: defensive parsing, io-bad, state, disk scan, sample meta."""

from __future__ import annotations

import json
import unittest

from fogmanager.core import catalog
from fogmanager.errors import CatalogError

from support import FixtureCase


class CatalogReadTest(FixtureCase):
    def test_read_category_index_fields(self) -> None:
        index = catalog.read_category_index(self.vfxdb_root / "AlphaWave")
        self.assertEqual(index.category, "AlphaWave")
        self.assertEqual(index.num_samples, 5)
        self.assertEqual(len(index.rows), 5)
        row = index.rows[0]
        self.assertEqual(row.id, "s0010__n0000")
        self.assertEqual(row.base_id, "s0010")
        self.assertEqual(row.seq, 10)
        self.assertEqual(row.folder, "t0")
        self.assertEqual(row.vdb_path, "t0/s0010__n0000.vdb")
        self.assertEqual(row.meta_path, "index/meta__s0010__n0000.json")
        self.assertEqual(row.size_bytes, 512)
        self.assertEqual(row.seq_key, "10")

    def test_both_vdb_path_forms(self) -> None:
        # t0/... and deeper folder names both parse (posix relative paths).
        index = catalog.read_category_index(self.vfxdb_root / "BetaFog")
        self.assertEqual(index.rows[0].vdb_path, "t0/s0000__n0000.vdb")

    def test_index_only_category(self) -> None:
        index = catalog.read_category_index(self.vfxdb_root / "EmptyCat")
        self.assertEqual(index.rows, [])
        self.assertEqual(index.num_samples, 0)

    def test_missing_index_raises_catalog_error(self) -> None:
        with self.assertRaises(CatalogError):
            catalog.read_category_index(self.vfxdb_root / "Ghost")

    def test_malformed_index_rejected(self) -> None:
        bad = self.base / "BadCat"
        bad.mkdir()
        (bad / "category_index.json").write_text(json.dumps({"samples": "nope"}))
        with self.assertRaises(CatalogError):
            catalog.read_category_index(bad)

    def test_read_state_and_pin(self) -> None:
        state = catalog.read_state(self.vfxdb_root)
        self.assertIsNotNone(state)
        self.assertIn("revision", state)
        self.assertEqual(catalog.pinned_revision(self.vfxdb_root), make_pin())
        # Local root has no state: optional everywhere.
        self.assertIsNone(catalog.read_state(self.local_root))
        self.assertIsNone(catalog.pinned_revision(self.local_root))

    def test_io_bad_sources(self) -> None:
        bad = catalog.read_io_bad_sources(self.vfxdb_root)
        self.assertEqual(bad, {"AlphaWave/t1/s0020__n0002.vdb"})
        self.assertIsNone(catalog.read_io_bad_sources(self.local_root))

    def test_sample_meta(self) -> None:
        meta = catalog.read_sample_meta(
            self.vfxdb_root / "AlphaWave", "index/meta__s0010__n0000.json"
        )
        self.assertEqual(meta.get("frame"), "s0010__n0000")
        with self.assertRaises(CatalogError):
            catalog.read_sample_meta(self.vfxdb_root / "AlphaWave", None)
        with self.assertRaises(CatalogError):
            catalog.read_sample_meta(self.vfxdb_root / "AlphaWave", "index/nothere.json")

    def test_scan_category_disk_skips_index(self) -> None:
        found = catalog.scan_category_disk(self.vfxdb_root / "AlphaWave")
        self.assertIn("t0/s0010__n0000.vdb", found)
        self.assertIn("t1/s0020__n0000.vdb", found)
        self.assertIn("t1/s0020__n0002.vdb", found)
        # index/ sidecar JSONs must not appear.
        self.assertFalse(any(k.startswith("index/") for k in found))
        # sizes are real file sizes
        self.assertEqual(found["t0/s0010__n0000.vdb"], 512)

    def test_list_category_dirs(self) -> None:
        names = catalog.list_category_dirs(self.vfxdb_root)
        self.assertEqual(names, ["AlphaWave", "BetaFog", "EmptyCat"])

    def test_sample_key_matches_manifest_shape(self) -> None:
        index = catalog.read_category_index(self.vfxdb_root / "AlphaWave")
        row = [r for r in index.rows if r.id == "s0020__n0002"][0]
        self.assertEqual(
            catalog.sample_key("AlphaWave", row), "AlphaWave/t1/s0020__n0002.vdb"
        )


def make_pin() -> str:
    import make_mini_root

    return make_mini_root.REVISION


if __name__ == "__main__":
    unittest.main()