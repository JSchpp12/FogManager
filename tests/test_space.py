"""Space math: human sizes, guard thresholds, fallback estimates."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fogmanager.errors import SpaceGuardError
from fogmanager.sources.vfxdb import space


class HumanBytesTest(unittest.TestCase):
    def test_units(self) -> None:
        self.assertEqual(space.human_bytes(0), "0 B")
        self.assertEqual(space.human_bytes(512), "512 B")
        self.assertEqual(space.human_bytes(1024), "1.0 KiB")
        self.assertEqual(space.human_bytes(5 * 1024**3), "5.0 GiB")
        self.assertEqual(space.human_bytes(1.5 * 1024**4), "1.5 TiB")


class GuardTest(unittest.TestCase):
    def setUp(self) -> None:
        # Guard math needs a filesystem with >10 GiB free (the margin math
        # crosses the guard below that); prefer home, skip if too small.
        base = Path.home()
        if space.free_bytes(base) < 8 * 1024**3:
            self.skipTest("filesystem too small for guard-threshold tests")
        self._tmp = tempfile.TemporaryDirectory(dir=str(base))
        self.root = Path(self._tmp.name)
        self.free = space.free_bytes(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_roomy_estimate_passes_silently(self) -> None:
        needed = self.free // 10
        self.assertIsNone(space.check_disk(needed, self.root))

    def test_over_80_percent_warns(self) -> None:
        needed = int(self.free * 0.9)
        warning = space.check_disk(needed, self.root)
        self.assertIsNotNone(warning)
        self.assertIn("80%", warning)

    def test_exceeding_free_refuses(self) -> None:
        needed = self.free  # + 1 GiB margin exceeds free
        with self.assertRaises(SpaceGuardError):
            space.check_disk(needed, self.root)

    def test_force_overrides_refusal(self) -> None:
        needed = self.free
        self.assertIsNone(space.check_disk(needed, self.root, force=True))

    def test_margin_is_one_gib(self) -> None:
        # free - 2 GiB stays under the 1 GiB margin (a high-usage warning may
        # still fire); free - 0.5 GiB crosses it and refuses.
        space.check_disk(self.free - 2 * 1024**3, self.root)
        with self.assertRaises(SpaceGuardError):
            space.check_disk(self.free - 512 * 1024**2, self.root)


class NeededBytesTest(unittest.TestCase):
    def test_known_remote_sizes(self) -> None:
        parts = space.estimate_parts(
            extracted_bytes=1000, all_rows=10, selected_tar_paths=["archives/A/t0.tar"]
        )
        remote = space.RemoteSizes(
            sizes={"archives/A/t0.tar": 1100, "meta/vfxdb_meta.tar.zst": 74_000_000},
            known=True,
        )
        needed = space.needed_bytes(
            parts, remote, meta_tar_size=remote.sizes["meta/vfxdb_meta.tar.zst"]
        )
        self.assertEqual(needed, 1000 + 1100 + 74_000_000)

    def test_fallback_adds_member_overhead_and_meta(self) -> None:
        parts = space.estimate_parts(
            extracted_bytes=1000, all_rows=10, selected_tar_paths=["archives/A/t0.tar"]
        )
        remote = space.RemoteSizes(sizes={}, known=False)
        needed = space.needed_bytes(parts, remote, meta_tar_size=None)
        self.assertEqual(needed, 1000 + (1000 + 1024 * 10) + space.FALLBACK_META_TAR_BYTES)


class CacheUsageTest(unittest.TestCase):
    def test_missing_and_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            used, exists = space.cache_usage(Path(tmp) / "nope")
            self.assertEqual((used, exists), (0, False))
            target = Path(tmp) / "cache"
            (target / "blobs").mkdir(parents=True)
            (target / "blobs" / "x").write_bytes(b"z" * 1000)
            (target / "blobs" / "y.incomplete").write_bytes(b"z" * 500)
            used, exists = space.cache_usage(target)
            self.assertEqual((used, exists), (1500, True))


if __name__ == "__main__":
    unittest.main()