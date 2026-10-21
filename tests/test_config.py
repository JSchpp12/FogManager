"""Config: round-trip, unknown kinds, env override, error reporting."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from fogmanager.core.config import (
    Config,
    LocalImportSourceConfig,
    UnknownSourceConfig,
    VfxdbSourceConfig,
    resolve_config_path,
)
from fogmanager.errors import ConfigError, SourceNotFoundError

from support import write_config


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.path = self.base / "config.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_load_missing_file_gives_uninitialized_config(self) -> None:
        config = Config.load(str(self.base / "nope.json"))
        self.assertEqual(config.sources, {})
        self.assertEqual(config.path, self.base / "nope.json")

    def test_round_trip(self) -> None:
        write_config(self.path, {})
        config = Config.load(str(self.path))
        entry = VfxdbSourceConfig(
            name="vfxdb", data_root="/data/vfxdb", cache_dir="/cache/hf",
            revision="8b2abbf",
        )
        entry.tool.sha256s["download_extract_data.py"] = "ab" * 32
        config.sources["vfxdb"] = entry
        config.sources["sims"] = LocalImportSourceConfig(
            name="sims", data_root="/data/sims", index_dir="/data/sims-index",
            imported_at="2026-10-19T00:00:00+00:00",
        )
        config.save()

        loaded = Config.load(str(self.path))
        self.assertIsInstance(loaded.sources["vfxdb"], VfxdbSourceConfig)
        self.assertEqual(loaded.sources["vfxdb"].data_root, "/data/vfxdb")
        self.assertEqual(
            loaded.sources["vfxdb"].tool.sha256s["download_extract_data.py"], "ab" * 32
        )
        self.assertIsInstance(loaded.sources["sims"], LocalImportSourceConfig)
        self.assertEqual(loaded.sources["sims"].index_dir, "/data/sims-index")

    def test_unknown_kind_preserved_and_flagged(self) -> None:
        write_config(self.path, {"weird": {"kind": "s3_object_store", "bucket": "x"}})
        config = Config.load(str(self.path))
        self.assertIsInstance(config.sources["weird"], UnknownSourceConfig)
        self.assertEqual(config.sources["weird"].kind, "s3_object_store")
        # Round-trips untouched (forward compatibility).
        config.save()
        raw = json.loads(self.path.read_text())
        self.assertEqual(raw["sources"]["weird"]["bucket"], "x")
        # But using it fails clearly.
        with self.assertRaises(ConfigError):
            config.find_vfxdb("weird")

    def test_bad_schema_version_rejected(self) -> None:
        self.path.write_text(json.dumps({"schema_version": 9, "sources": {}}))
        with self.assertRaises(ConfigError):
            Config.load(str(self.path))

    def test_invalid_json_rejected(self) -> None:
        self.path.write_text("{nope")
        with self.assertRaises(ConfigError):
            Config.load(str(self.path))

    def test_local_import_requires_data_root(self) -> None:
        write_config(self.path, {"sims": {"kind": "local_import"}})
        with self.assertRaises(ConfigError):
            Config.load(str(self.path))

    def test_find_vfxdb_defaults_and_conflicts(self) -> None:
        write_config(self.path, {})
        config = Config.load(str(self.path))
        with self.assertRaises(SourceNotFoundError):
            config.find_vfxdb()

        config.sources["a"] = VfxdbSourceConfig(name="a", data_root="x", cache_dir="y")
        self.assertEqual(config.find_vfxdb().name, "a")

        config.sources["b"] = VfxdbSourceConfig(name="b", data_root="x", cache_dir="y")
        with self.assertRaises(ConfigError):
            config.find_vfxdb()
        self.assertEqual(config.find_vfxdb("b").name, "b")

    def test_env_override(self) -> None:
        original = os.environ.get("FOGMGR_CONFIG")
        try:
            os.environ["FOGMGR_CONFIG"] = str(self.path)
            self.assertEqual(resolve_config_path(), self.path)
        finally:
            if original is None:
                os.environ.pop("FOGMGR_CONFIG", None)
            else:
                os.environ["FOGMGR_CONFIG"] = original

    def test_get_unknown_source(self) -> None:
        write_config(self.path, {})
        config = Config.load(str(self.path))
        with self.assertRaises(SourceNotFoundError):
            config.get("ghost")


if __name__ == "__main__":
    unittest.main()