"""Local import: synthesized rows, id rule, index-dir, re-index, registration."""

from __future__ import annotations

import json
import unittest

from fogmanager.core.config import LocalImportSourceConfig
from fogmanager.errors import ConfigError, IndexExistsError

from support import FixtureCase


class ImportTest(FixtureCase):
    def setUp(self) -> None:
        super().setUp()
        # Clean import slate: drop the auto-imported 'sims' source and the
        # indexes it wrote, so these tests exercise fresh imports.
        for index_file in self.local_root.rglob("category_index.json"):
            index_file.unlink()
        config = self.fresh_config()
        del config.sources["sims"]
        config.save()

    def test_import_synthesizes_vfxdb_rows(self) -> None:
        result = self.client().import_source("sims2", self.local_root)
        self.assertEqual(result.source, "sims2")
        self.assertEqual(result.total_samples, 5)  # 4 gamma + 1 delta
        self.assertEqual(result.skipped_files, 1)  # notes.txt

        index_file = self.local_root / "GammaMist" / "category_index.json"
        payload = json.loads(index_file.read_text())
        self.assertEqual(payload["category"], "GammaMist")
        self.assertEqual(payload["num_samples"], 4)
        row = payload["samples"][0]
        self.assertEqual(sorted(row), ["base_id", "folder", "id", "meta_path",
                                       "mtime", "seq", "size_bytes", "vdb_path"])
        self.assertEqual(row["vdb_path"], "seqA/a_one.vdb")
        self.assertEqual(row["seq"], "seqA")
        self.assertEqual(row["folder"], "seqA")
        self.assertIsNone(row["meta_path"])
        self.assertIsInstance(row["size_bytes"], int)
        self.assertIsInstance(row["mtime"], int)

        # Real file sizes recorded.
        st = (self.local_root / "GammaMist" / "seqA" / "a_one.vdb").stat()
        self.assertEqual(row["size_bytes"], st.st_size)
        self.assertEqual(row["mtime"], int(st.st_mtime))

    def test_id_stays_stem_when_unique(self) -> None:
        self.client().import_source("s", self.local_root)
        payload = json.loads(
            (self.local_root / "GammaMist" / "category_index.json").read_text()
        )
        ids = {row["id"] for row in payload["samples"]}
        self.assertIn("a_one", ids)
        self.assertIn("b_one", ids)

    def test_repeat_stem_becomes_seq_prefixed_id(self) -> None:
        # a_two exists in seqA and seqB, so both occurrences are disambiguated
        # uniformly as <seq>/<stem> (documented id synthesis rule).
        self.client().import_source("s", self.local_root)
        payload = json.loads(
            (self.local_root / "GammaMist" / "category_index.json").read_text()
        )
        ids = sorted(row["id"] for row in payload["samples"])
        self.assertEqual(ids, ["a_one", "b_one", "seqA/a_two", "seqB/a_two"])

    def test_index_dir_relocation(self) -> None:
        reloc = self.base / "readonly-indexes"
        self.client().import_source("s", self.local_root, index_dir=reloc)
        self.assertFalse(
            (self.local_root / "GammaMist" / "category_index.json").exists()
        )
        self.assertTrue((reloc / "GammaMist" / "category_index.json").is_file())
        config = self.fresh_config()
        entry = config.sources["s"]
        self.assertEqual(entry.index_dir, str(reloc))

    def test_re_index_flag_required_to_rewrite(self) -> None:
        self.client().import_source("s", self.local_root)
        with self.assertRaises(IndexExistsError):
            self.client().import_source("s2", self.local_root)
        # Same name + --re-index re-walks fine.
        result = self.client().import_source("s", self.local_root, re_index=True)
        self.assertEqual(result.total_samples, 5)

    def test_source_registered_in_config(self) -> None:
        self.client().import_source("s", self.local_root)
        config = self.fresh_config()
        entry = config.sources["s"]
        self.assertIsInstance(entry, LocalImportSourceConfig)
        self.assertEqual(entry.data_root, str(self.local_root.resolve()))
        self.assertIsNotNone(entry.imported_at)

    def test_category_filter(self) -> None:
        result = self.client().import_source(
            "s", self.local_root, categories=["DeltaHaze"]
        )
        self.assertEqual([e["category"] for e in result.per_category], ["DeltaHaze"])

    def test_filtered_empty_category_is_clear_error(self) -> None:
        with self.assertRaises(ConfigError):
            self.client().import_source("s", self.local_root, categories=["Nope"])

    def test_root_must_exist(self) -> None:
        with self.assertRaises(ConfigError):
            self.client().import_source("s", self.base / "ghost-root")

    def test_no_categories_found(self) -> None:
        empty = self.base / "nothing-here"
        empty.mkdir()
        with self.assertRaises(ConfigError):
            self.client().import_source("s", empty)

    def test_name_conflict(self) -> None:
        self.client().import_source("s", self.local_root)
        with self.assertRaises(ConfigError):
            self.client().import_source("vfxdb", self.local_root)
        # But re_index on the existing local source is allowed (tested above).

    def test_local_source_reads_its_index(self) -> None:
        self.client().import_source("s", self.local_root)
        client = self.client()
        categories = client.categories(source="s")
        names = {c.name for c in categories}
        self.assertEqual(names, {"DeltaHaze", "GammaMist"})
        samples = client.samples("GammaMist", source="s")
        self.assertEqual(len(samples), 4)
        self.assertTrue(all(s.present for s in samples))


if __name__ == "__main__":
    unittest.main()