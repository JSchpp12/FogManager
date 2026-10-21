"""Queries through api.Client: merged multi-source reads + status uniformity."""

from __future__ import annotations

import unittest

from fogmanager.api import Client
from fogmanager.core.model import Sample, StatusReport
from fogmanager.errors import UnknownCategoryError, UnknownSampleError

from support import FixtureCase


class SourcesTest(FixtureCase):
    def test_sources_listing(self) -> None:
        infos = self.client().sources()
        by_name = {s.name: s for s in infos}
        self.assertEqual(by_name["vfxdb"].kind, "vfxdb_hf")
        self.assertTrue(by_name["vfxdb"].can_download)
        self.assertFalse(by_name["sims"].can_download)

    def test_unknown_kind_listed_as_unusable(self) -> None:
        # Add the unknown-kind entry directly to the config *file* (as another
        # application writing forward-compatible kinds would).
        import json

        raw = json.loads(self.config_path.read_text())
        raw["sources"]["s3"] = {"kind": "s3_object_store", "bucket": "x"}
        self.config_path.write_text(json.dumps(raw))
        infos = Client.load(str(self.config_path)).sources()
        entry = [s for s in infos if s.name == "s3"][0]
        self.assertFalse(entry.usable)
        self.assertIn("unknown kind", entry.detail)


class CategoriesTest(FixtureCase):
    def test_merged_categories_tagged_by_source(self) -> None:
        summaries = self.client().categories()
        tags = {(c.source, c.name) for c in summaries}
        self.assertIn(("vfxdb", "AlphaWave"), tags)
        self.assertIn(("sims", "GammaMist"), tags)
        alpha = [c for c in summaries if c.name == "AlphaWave"][0]
        self.assertEqual(alpha.num_samples, 5)
        self.assertEqual(alpha.sequences, 2)
        self.assertEqual(alpha.vdb_total, 5)
        self.assertEqual(alpha.vdb_present, 4)
        self.assertFalse(alpha.complete)

    def test_source_scoped(self) -> None:
        sims = self.client().categories(source="sims")
        self.assertEqual({c.source for c in sims}, {"sims"})
        self.assertEqual(len(sims), 2)

    def test_unknown_source(self) -> None:
        with self.assertRaises(Exception) as ctx:
            self.client().categories(source="ghost")
        self.assertIn("ghost", str(ctx.exception))


class SequencesSamplesTest(FixtureCase):
    def test_sequences_rollup(self) -> None:
        seqs = self.client().sequences("AlphaWave")
        by_name = {s.name: s for s in seqs}
        self.assertEqual(by_name["10"].complete, True)
        self.assertEqual(by_name["10"].num_samples, 2)
        self.assertFalse(by_name["20"].complete)
        self.assertEqual(by_name["20"].vdb_present, 2)
        self.assertEqual(by_name["20"].vdb_total, 3)
        self.assertEqual(by_name["20"].folders, ["t1"])

    def test_sequences_of_local_source(self) -> None:
        seqs = self.client().sequences("GammaMist", source="sims")
        self.assertEqual({s.name for s in seqs}, {"seqA", "seqB"})

    def test_samples_and_limit(self) -> None:
        samples = self.client().samples("AlphaWave")
        self.assertEqual(len(samples), 5)
        limited = self.client().samples("AlphaWave", limit=2)
        self.assertEqual(len(limited), 2)
        first = limited[0]
        self.assertIsInstance(first, Sample)
        self.assertTrue(str(first.path).endswith("t0/s0010__n0000.vdb"))
        self.assertTrue(first.present)

    def test_sequence_filter_and_missing_marker(self) -> None:
        samples = self.client().samples("AlphaWave", sequence="20")
        self.assertEqual({s.id for s in samples}, {"s0020__n0000", "s0020__n0001", "s0020__n0002"})
        marks = {s.id: s.present for s in samples}
        self.assertEqual(marks["s0020__n0000"], True)
        self.assertEqual(marks["s0020__n0001"], False)
        self.assertEqual(marks["s0020__n0002"], True)

    def test_empty_category_ok(self) -> None:
        self.assertEqual(self.client().samples("EmptyCat"), [])
        self.assertEqual(self.client().sequences("EmptyCat"), [])

    def test_unknown_category_raises(self) -> None:
        with self.assertRaises(UnknownCategoryError):
            self.client().sequences("Ghost")


class PathsTest(FixtureCase):
    def test_paths_resolves_id(self) -> None:
        samples = self.client().paths("s0020__n0002", "AlphaWave", "vfxdb")
        self.assertEqual(len(samples), 1)
        self.assertTrue(samples[0].path.endswith("t1/s0020__n0002.vdb"))

    def test_paths_fallback_to_stem(self) -> None:
        samples = self.client().paths("s0010__n0001", "AlphaWave", "vfxdb")
        self.assertEqual(len(samples), 1)

    def test_paths_unknown_id(self) -> None:
        with self.assertRaises(UnknownSampleError):
            self.client().paths("nada", "AlphaWave", "vfxdb")

    def test_sample_meta(self) -> None:
        meta = self.client().sample_meta("s0010__n0000", "AlphaWave", "vfxdb")
        self.assertEqual(meta["frame"], "s0010__n0000")


class StatusTest(FixtureCase):
    def test_status_uniform_across_sources(self) -> None:
        report = self.client().status()
        self.assertIsInstance(report, StatusReport)
        by_source = {s.source: s for s in report.sources}
        self.assertIn("vfxdb", by_source)
        self.assertIn("sims", by_source)

        alpha = [c for c in by_source["vfxdb"].categories if c.name == "AlphaWave"][0]
        self.assertTrue(alpha.index_present)
        self.assertEqual(alpha.vdb_present, 4)
        self.assertEqual(alpha.vdb_total, 5)
        self.assertEqual(alpha.sequences_complete, 1)
        self.assertEqual(alpha.sequences_partial, 1)
        self.assertEqual(alpha.sequences_absent, 0)

        # Local source: no vfxdb-only extras.
        self.assertIsNone(by_source["sims"].pinned_revision)
        self.assertIsNone(by_source["sims"].cache_bytes)
        # vfxdb source surfaces the pinned revision.
        self.assertEqual(by_source["vfxdb"].pinned_revision, make_pin())

    def test_status_category_scoped(self) -> None:
        report = self.client().status(category="BetaFog")
        entries = [c for s in report.sources for c in s.categories]
        self.assertEqual([c.name for c in entries], ["BetaFog"])
        self.assertEqual(entries[0].sequences_complete, 1)

    def test_indexless_category_reports_walk(self) -> None:
        (self.local_root / "GammaMist" / "category_index.json").unlink()
        report = self.client().status(source="sims", category="GammaMist")
        cat = report.sources[0].categories[0]
        self.assertFalse(cat.index_present)
        self.assertEqual(cat.disk_walk_total, 4)

    def test_totals_helper(self) -> None:
        report = self.client().status(source="vfxdb")
        complete, partial, absent, present = report.sources[0].totals()
        # AlphaWave: 1 complete + 1 partial; BetaFog: 1 complete; EmptyCat: none.
        self.assertEqual((complete, partial, absent), (2, 1, 0))
        self.assertEqual(present, 5)


def make_pin():
    import make_mini_root

    return make_mini_root.REVISION


if __name__ == "__main__":
    unittest.main()