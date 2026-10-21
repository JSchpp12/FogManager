"""``fogmgr import`` — ingest generated data in the import contract shape."""

from __future__ import annotations

import argparse

from .common import human_bytes


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("import", help="import generated <Category>/<sequence>/<sample>.vdb data")
    parser.add_argument("name", metavar="NAME", help="source name to register")
    parser.add_argument("path", metavar="PATH", help="root containing <Category>/<sequence>/<sample>.vdb")
    parser.add_argument("--category", action="append", default=[], metavar="NAME",
                        help="only index these categories (repeatable)")
    parser.add_argument("--index-dir", metavar="PATH",
                        help="write indexes here instead of next to the data (read-only trees)")
    parser.add_argument("--re-index", action="store_true",
                        help="re-walk and rewrite indexes for an existing source")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace, client) -> int:
    result = client.import_source(
        args.name,
        args.path,
        categories=args.category or None,
        index_dir=args.index_dir,
        re_index=args.re_index,
    )
    if args.json:
        from .common import emit_json

        emit_json(result)
        return 0
    print(f"source {result.source!r} registered")
    print(f"  root      : {result.root}")
    print(f"  index_dir : {result.index_dir or '(next to the data)'}")
    for entry in result.per_category:
        print(
            f"  {entry['category']}: {entry['samples']:,} samples in "
            f"{entry['sequences']} sequence(s), {human_bytes(entry['bytes'])}"
        )
        print(f"    index: {entry['index_path']}")
    if result.skipped_files:
        print(f"  skipped {result.skipped_files} non-.vdb file(s)")
    print(f"  total: {result.total_samples:,} samples, {human_bytes(result.total_bytes)}")
    return 0