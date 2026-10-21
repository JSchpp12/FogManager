"""CLI: validation, JSON output shape, exit-code mapping."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from fogmanager.sources.vfxdb import official

from support import FixtureCase, run_cli


class CliReadCommandsTest(FixtureCase):
    def status(self) -> dict:
        code, out, err = run_cli(["status", "--json"], self.config_path)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_status_json_matches_api_to_dict(self) -> None:
        report = self.status()
        by_source = {s["source"]: s for s in report["sources"]}
        self.assertIn("vfxdb", by_source)
        self.assertIn("sims", by_source)
        alpha = [c for c in by_source["vfxdb"]["categories"] if c["name"] == "AlphaWave"][0]
        self.assertEqual(alpha["vdb_present"], 4)
        self.assertEqual(alpha["vdb_total"], 5)
        self.assertEqual(by_source["vfxdb"]["pinned_revision"], make_pin())

    def test_status_plain_text(self) -> None:
        code, out, err = run_cli(["status"], self.config_path)
        self.assertEqual(code, 0, err)
        self.assertIn("complete", out)
        self.assertIn("AlphaWave", out)

    def test_list_categories_json_and_upstream_table(self) -> None:
        code, out, _ = run_cli(["list-categories", "--json"], self.config_path)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertTrue(any(c["name"] == "GammaMist" for c in data))

        code, out, _ = run_cli(["list-categories", "--source", "sims"], self.config_path)
        self.assertEqual(code, 0)

    def test_inspect_json(self) -> None:
        code, out, err = run_cli(["inspect", "AlphaWave", "--json"], self.config_path)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["totals"]["num_samples"], 5)
        self.assertEqual(len(data["samples"]), 5)
        names = {s["name"] for s in data["sequences"]}
        self.assertEqual(names, {"10", "20"})

    def test_inspect_sample_meta_raw_dump(self) -> None:
        code, out, _ = run_cli(
            ["inspect", "AlphaWave", "--sample", "s0010__n0000"], self.config_path
        )
        self.assertEqual(code, 0)
        self.assertIn('"frame"', out)  # raw meta JSON rendered
        self.assertIn("s0010__n0000", out)

    def test_sources_json(self) -> None:
        code, out, _ = run_cli(["sources", "--json"], self.config_path)
        self.assertEqual(code, 0)
        data = json.loads(out)
        kinds = {s["name"]: s["kind"] for s in data}
        self.assertEqual(kinds["vfxdb"], "vfxdb_hf")
        self.assertEqual(kinds["sims"], "local_import")

    def test_import_command_end_to_end(self) -> None:
        code, out, err = run_cli(
            ["import", "treesim", str(self.local_root), "--json"], self.config_path
        )
        # 'treesim' root has pre-existing indexes from fixture setup -> refuse.
        self.assertEqual(code, 1)
        self.assertIn("--re-index", err)

    def test_download_dry_run_never_runs_tool(self) -> None:
        from fogmanager.sources.vfxdb import space

        # The estimate path needs a fetched tool + a stubbed remote listing.
        config = self.fresh_config()
        stub_tool = self.base / "tools" / "download_extract_data.py"
        stub_tool.parent.mkdir(parents=True, exist_ok=True)
        stub_tool.write_text("# bootstrap\n", encoding="utf-8")
        config.sources["vfxdb"].tool.path = str(stub_tool)
        config.save()

        with mock.patch.object(official, "run_official") as run_official_mock, \
                mock.patch(
                    "fogmanager.sources.vfxdb.space.fetch_remote_sizes",
                    return_value=space.RemoteSizes(sizes={}, known=False),
                ) as remote_sizes:
            code, out, err = run_cli(
                ["download", "--category", "AlphaWave", "--max-samples", "1",
                 "--dry-run"],
                self.config_path,
            )
        self.assertEqual(code, 0, err)
        self.assertIn("estimate", out.lower())
        self.assertIn("--cache-dir", out)
        self.assertIn("≈", out)
        run_official_mock.assert_not_called()
        remote_sizes.assert_called_once()


class CliValidationTest(FixtureCase):
    def test_category_requires_max_samples(self) -> None:
        code, _, err = run_cli(["download", "--category", "A"], self.config_path)
        self.assertEqual(code, 2)
        self.assertIn("--max-samples", err)

    def test_max_samples_requires_category(self) -> None:
        code, _, err = run_cli(["download", "--max-samples", "5"], self.config_path)
        self.assertEqual(code, 2)

    def test_mutually_exclusive_modes(self) -> None:
        code, _, _ = run_cli(
            ["download", "--preset", "smoke", "--percentage", "10"], self.config_path
        )
        self.assertEqual(code, 2)

    def test_unknown_command_fails(self) -> None:
        code, _, err = run_cli(["nope"], self.config_path)
        self.assertEqual(code, 2)

    def test_fogmanager_errors_exit_1(self) -> None:
        code, _, err = run_cli(
            ["inspect", "GhostCat"], self.config_path
        )
        self.assertEqual(code, 1)
        self.assertIn("error:", err)


def make_pin():
    import make_mini_root

    return make_mini_root.REVISION


if __name__ == "__main__":
    unittest.main()