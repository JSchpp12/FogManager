"""``fogmgr status`` — per-source and per-category completeness report."""

from __future__ import annotations

import argparse

from .common import human_bytes


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("status", help="download/completeness status per source")
    parser.add_argument("--source", metavar="NAME")
    parser.add_argument("--category", metavar="NAME")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace, client) -> int:
    report = client.status(source=args.source, category=args.category)
    if args.json:
        from .common import emit_json

        emit_json(report)
        return 0
    if not report.sources:
        print("no usable sources registered; run 'fogmgr init' or 'fogmgr import'")
        return 0
    for status in report.sources:
        complete, partial, absent, present = status.totals()
        print(f"source {status.source} ({status.kind})")
        print(f"  data_root: {status.data_root}   free: {human_bytes(status.free_bytes)}")
        if status.pinned_revision is not None:
            print(f"  pinned revision: {status.pinned_revision}")
        if status.cache_bytes is not None:
            print(f"  HF cache: {human_bytes(status.cache_bytes)}")
        if not status.categories:
            print("  (no categories — is metadata installed / data imported?)")
        for cat in status.categories:
            total = cat.sequences_complete + cat.sequences_partial + cat.sequences_absent
            print(f"  {cat.name}:")
            if cat.index_present:
                print(
                    f"    sequences: {cat.sequences_complete} complete, "
                    f"{cat.sequences_partial} partial, {cat.sequences_absent} absent "
                    f"(of {total})"
                )
                print(
                    f"    vdb files: {cat.vdb_present}/{cat.vdb_total}   "
                    f"bytes on disk: {human_bytes(cat.disk_bytes)}"
                )
            else:
                print(f"    no index; raw walk found {cat.disk_walk_total} .vdb file(s)")
        print(
            f"  total: {complete} complete, {partial} partial, {absent} absent sequences, "
            f"{present} vdb files present"
        )
    return 0