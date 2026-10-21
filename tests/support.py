"""Shared helpers for the unittest suite (stdlib only)."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(FIXTURES))

import make_mini_root  # noqa: E402 - fixtures dir on sys.path

from fogmanager.api import Client  # noqa: E402
from fogmanager.core.config import Config  # noqa: E402


class FixtureCase(unittest.TestCase):
    """Base case: builds both fixture roots + a client with a temp config."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.vfxdb_root = make_mini_root.build_fake_vfxdb_root(base / "vfxdb")
        self.local_root = make_mini_root.build_local_root(base / "local")
        self.base = base
        self.config_path = base / "config.json"
        config = Config.load(str(self.config_path))
        assert config.path is not None
        config.sources["vfxdb"] = _vfxdb_entry(self.vfxdb_root, base / "hf-cache")
        config.save()
        # Register the local source by importing it (indexes written, source
        # registered in config) — the state merged tests rely on.
        client = Client.load(str(self.config_path))
        client.import_source(_local_entry(self.local_root).name, self.local_root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def client(self) -> Client:
        return Client.load(str(self.config_path))

    def fresh_config(self) -> Config:
        """Reload the config from disk (picks up side effects of a command)."""
        return Config.load(str(self.config_path))


def _vfxdb_entry(root: Path, cache: Path):
    from fogmanager.core.config import VfxdbSourceConfig

    return VfxdbSourceConfig(name="vfxdb", data_root=str(root), cache_dir=str(cache))


def _local_entry(root: Path):
    from fogmanager.core.config import LocalImportSourceConfig

    return LocalImportSourceConfig(name="sims", data_root=str(root))


def run_cli(argv: list[str], config_path: str | Path):
    """Run cli.main with a fixture config; capture output; return (code, out, err)."""
    import fogmanager.cli as cli

    out, err = io.StringIO(), io.StringIO()
    argv = ["--config", str(config_path)] + list(argv)
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:  # argparse errors
            code = exc.code
    return int(code or 0), out.getvalue(), err.getvalue()


def write_config(path: Path, sources: dict) -> Path:
    path.write_text(json.dumps({"schema_version": 1, "sources": sources}) + "\n",
                    encoding="utf-8")
    return path