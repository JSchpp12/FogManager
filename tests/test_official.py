"""Official-tool seam: argv invariants, display quoting, exit-code propagation."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fogmanager.core.config import VfxdbSourceConfig
from fogmanager.errors import ToolError
from fogmanager.sources.vfxdb import official
from fogmanager.sources.vfxdb.source import VfxdbSource


class BuildArgvTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self._tmp.name) / "data"
        self.cache = Path(self._tmp.name) / "cache"
        self.data_root.mkdir()
        self.cache.mkdir()
        tool_path = Path(self._tmp.name) / "tools" / "download_extract_data.py"
        tool_path.parent.mkdir(parents=True)
        tool_path.write_text("# bootstrap\n", encoding="utf-8")
        self.tool = str(tool_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def argv(self, **kw):
        kw.setdefault("tool_path", self.tool)
        return official.build_argv(self.data_root, self.cache, **kw)

    def test_shape_and_absolute_paths(self) -> None:
        argv = self.argv()
        self.assertEqual(argv[1], "-I")
        self.assertEqual(argv[2], self.tool)
        self.assertEqual(argv[3], str(self.data_root.resolve()))
        self.assertEqual(argv[4], "--cache-dir")
        self.assertEqual(argv[5], str(self.cache.resolve()))
        # Always passes --cache-dir (bootstrap code blobs must land in our cache).
        self.assertIn("--cache-dir", argv)

    def test_category_mode(self) -> None:
        argv = self.argv(categories=("RisingFlame", "SmokePlume"), max_samples=5)
        tail = argv[6:]
        self.assertEqual(tail, ["--max-samples", "5",
                                "--category", "RisingFlame",
                                "--category", "SmokePlume"])

    def test_include_bad_any_mode(self) -> None:
        argv = self.argv(include_bad=True)
        self.assertNotIn("--tui", argv)  # never pass --tui
        self.assertIn("--include-bad", argv)

    def test_revision_optional(self) -> None:
        self.assertNotIn("--revision", self.argv())
        argv = self.argv(revision="deadbeef")
        self.assertEqual(argv[argv.index("--revision") + 1], "deadbeef")

    def test_mutual_exclusion(self) -> None:
        with self.assertRaises(ToolError):
            self.argv(preset="smoke", percentage="10")
        with self.assertRaises(ToolError):
            self.argv(preset="smoke", categories=("A",), max_samples=1)
        with self.assertRaises(ToolError):
            self.argv(categories=("A",), max_samples=None)
        with self.assertRaises(ToolError):
            self.argv(max_samples=3)  # without categories
        with self.assertRaises(ToolError):
            self.argv(preset="bogus")

    def test_missing_tool_refused_upfront(self) -> None:
        with self.assertRaises(ToolError):
            official.build_argv(self.data_root, self.cache, tool_path="/nope/tool.py")


class RunOfficialTest(unittest.TestCase):
    def test_exit_code_propagates(self) -> None:
        with mock.patch("fogmanager.sources.vfxdb.official.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=7)
            code = official.run_official(["python", "-I", "tool.py", "x"])
        self.assertEqual(code, 7)
        run.assert_called_once_with(["python", "-I", "tool.py", "x"], check=False)

    def test_exit_codes_documented(self) -> None:
        self.assertEqual(official.EXIT_MEANINGS[0], "success")
        self.assertIn("Ctrl-C", official.EXIT_MEANINGS[130])
        self.assertIn("non-interactive", official.EXIT_MEANINGS[2])

    def test_display_command_posix(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX quoting rules")
        text = official.display_command(["python", "-I", "tool it.py", "a b"])
        self.assertEqual(text, "python -I 'tool it.py' 'a b'")


class FetchToolTest(unittest.TestCase):
    def test_fetch_copies_and_hashes(self) -> None:
        # Offline-safe: fake the hub download by pre-seeding a file.
        with tempfile.TemporaryDirectory() as tmp:
            hub_snapshot = Path(tmp) / "hub" / "snapshots" / "x" / "vfxdb_downloader.py"
            hub_snapshot.parent.mkdir(parents=True)
            hub_snapshot.write_text("stub", encoding="utf-8")
            tools = Path(tmp) / "tools"

            from unittest import mock

            def fake_download(**kw):
                self.assertEqual(kw["revision"], "main")
                self.assertEqual(kw["cache_dir"], str(Path(tmp) / "cache"))
                self.assertEqual(kw["repo_type"], "dataset")
                dl = Path(tmp) / "hub" / "snapshots" / "x" / "download_extract_data.py"
                dl.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
                return str(dl)

            with mock.patch("huggingface_hub.hf_hub_download", side_effect=fake_download):
                state = official.fetch_tool(
                    tools_dir=tools, cache_dir=Path(tmp) / "cache"
                )
            self.assertTrue((tools / "download_extract_data.py").is_file())
            self.assertIn("download_extract_data.py", state.sha256s)
            self.assertIn("vfxdb_downloader.py", state.sha256s)
            self.assertEqual(state.fetched_revision, "main")
            self.assertTrue(state.changed)

    def test_second_call_without_refresh_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools = Path(tmp) / "tools"
            tools.mkdir()
            target = tools / "download_extract_data.py"
            target.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            state = official.fetch_tool(tools_dir=tools, cache_dir=Path(tmp) / "c")
            self.assertFalse(state.changed)
            self.assertIsNone(state.fetched_revision)

    def test_config_records_tool_even_without_refresh(self) -> None:
        """Regression: a no-change fetch must still persist tool provenance
        (path + sha256s), or later commands can't resolve the tool — found
        in E2E when the repo copy already existed."""
        from fogmanager.api import Client

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            tools = base / "tools"
            cache = base / "cache"
            target = tools / "download_extract_data.py"
            tools.mkdir()
            cache.mkdir()
            target.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            config_path = base / "config.json"
            config = Client.load(str(config_path)).config
            entry = VfxdbSourceConfig(
                name="vfxdb", data_root=str(base / "data"), cache_dir=str(cache)
            )
            entry.tool.path = str(target)  # tool known, provenance not yet
            config.sources["vfxdb"] = entry
            config.save()

            source_client = Client.load(str(config_path))
            source = VfxdbSource(source_client.config, source_client.config.sources["vfxdb"])
            state = source.fetch_tool()
            self.assertFalse(state.changed)

            recorded = Client.load(str(config_path)).config.sources["vfxdb"].tool
            self.assertEqual(recorded.path, str(target))
            self.assertIn("download_extract_data.py", recorded.sha256s)


if __name__ == "__main__":
    unittest.main()