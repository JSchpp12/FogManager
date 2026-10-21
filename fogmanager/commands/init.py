"""``fogmgr init`` — create config + the default vfxdb source entry."""

from __future__ import annotations

import argparse
from pathlib import Path

import platformdirs

from ..core.config import Config, DEFAULT_VFXDB_SOURCE, VfxdbSourceConfig
from ..errors import ConfigError


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "init", help="create the FogManager config and default vfxdb source"
    )
    parser.add_argument("--data-root", required=True, metavar="PATH",
                        help="absolute data root for the vfxdb source")
    parser.add_argument("--cache-dir", metavar="PATH",
                        help="HF cache dir (default: <user cache>/FogManager/hf-cache)")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing vfxdb entry")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace, client) -> int:
    config: Config = client.config
    if config.sources and not args.force:
        raise ConfigError(
            f"config {config.path} already has sources; pass --force to overwrite the vfxdb entry"
        )
    data_root = Path(args.data_root).expanduser().resolve()
    cache_dir = (
        Path(args.cache_dir).expanduser().resolve()
        if args.cache_dir
        else Path(platformdirs.user_cache_dir("FogManager", appauthor=False)) / "hf-cache"
    )
    data_root.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    entry = config.sources.get(DEFAULT_VFXDB_SOURCE)
    if not isinstance(entry, VfxdbSourceConfig):
        entry = VfxdbSourceConfig(name=DEFAULT_VFXDB_SOURCE)
    entry.data_root = str(data_root)
    entry.cache_dir = str(cache_dir)
    config.sources[DEFAULT_VFXDB_SOURCE] = entry
    saved = config.save()
    print(f"config: {saved}")
    print(f"vfxdb data_root: {data_root}")
    print(f"vfxdb cache_dir: {cache_dir}")
    print("next: fogmgr doctor && fogmgr fetch-tool")
    return 0