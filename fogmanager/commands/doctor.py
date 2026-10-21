"""``fogmgr doctor`` — deps/tool/metadata report; ``--fix`` runs pip."""

from __future__ import annotations

import argparse

from .. import deps
from ..core.config import VfxdbSourceConfig


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("doctor", help="report environment, tool, and metadata health")
    parser.add_argument("--fix", action="store_true", help="pip-install missing dependencies")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace, client) -> int:
    from .common import human_bytes

    problems = 0

    print("dependencies:")
    for dep in deps.probe():
        status = "ok" if dep.ok else f"MISSING ({dep.error})"
        problems += 0 if dep.ok else 1
        print(f"  {dep.requirement:<32} {status}" + (f"  {dep.version}" if dep.version else ""))

    vfxdb = None
    for entry in client.config.sources.values():
        if isinstance(entry, VfxdbSourceConfig):
            vfxdb = entry
            break

    print("tool:")
    if vfxdb is None:
        print("  no vfxdb source registered; run 'fogmgr init'")
        problems += 1
    else:
        tool_path = vfxdb.tool.path
        if tool_path:
            from pathlib import Path

            from ..sources.vfxdb.official import sha256_file

            path = Path(tool_path)
            recorded = vfxdb.tool.sha256s.get("download_extract_data.py")
            if path.is_file():
                actual = sha256_file(path)
                match = "?" if recorded is None else ("ok" if actual == recorded else "MISMATCH")
                if match == "MISMATCH":
                    problems += 1
                print(f"  {path}  sha256 {match}")
            else:
                print(f"  {path}  MISSING (run 'fogmgr fetch-tool')")
                problems += 1
        else:
            print("  not fetched yet (run 'fogmgr fetch-tool')")
            problems += 1

    print("metadata:")
    if vfxdb is None or not vfxdb.data_root:
        print("  no data_root configured")
        problems += 1
    else:
        from pathlib import Path

        from ..core import catalog

        root = Path(vfxdb.data_root)
        categories = catalog.list_category_dirs(root)
        cache_dir = Path(vfxdb.cache_dir) if vfxdb.cache_dir else None
        cache_bytes, cache_exists = (
            (0, False) if cache_dir is None else _cache_size(cache_dir)
        )
        print(f"  data_root: {root}  categories with index: {len(categories)}")
        pinned = catalog.pinned_revision(root)
        print(f"  pinned revision: {pinned or 'none'}")
        print(
            f"  cache_dir: {cache_dir}  "
            + (f"{human_bytes(cache_bytes)}" if cache_exists else "missing (created on first use)")
        )
        if not categories:
            print("  no category indexes installed; run 'fogmgr install-metadata'")

    if args.fix:
        print(f"running: {deps.pip_command()}")
        code, lines = deps.fix()
        for line in lines:
            print(f"  $ {line}")
        if code != 0:
            problems += 1

    print("doctor: NOT OK" if problems else "doctor: OK")
    return 1 if problems else 0


def _cache_size(cache_dir):
    from ..sources.vfxdb.space import cache_usage

    return cache_usage(cache_dir)