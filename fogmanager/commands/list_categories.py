"""``fogmgr list-categories`` — merged category listing across sources."""

from __future__ import annotations

import argparse

from .common import human_bytes


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("list-categories", help="list categories per source")
    parser.add_argument("--source", metavar="NAME")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace, client) -> int:
    from ..sources.vfxdb.official import OFFICIAL_CATEGORIES

    summaries = client.categories(source=args.source)
    if args.json:
        from .common import emit_json

        emit_json(summaries)
        return 0

    # The vfxdb source without installed metadata has no local indexes; print
    # the hardcoded upstream table so users know what can be installed.
    shown_sources = {s.name for s in client.sources() if s.usable and (args.source is None or s.name == args.source)}
    vfxdb_without_meta = [
        name
        for name in shown_sources
        if any(s.kind == "vfxdb_hf" and s.name == name for s in client.sources())
        and not any(c.source == name for c in summaries)
    ]
    for name in vfxdb_without_meta:
        print(f"source {name}: metadata not installed. Upstream categories available:")
        for category in OFFICIAL_CATEGORIES:
            print(f"  {category}")
        print("  (run 'fogmgr install-metadata' to install indexes)")

    for summary in summaries:
        state = "complete" if summary.complete else f"{summary.vdb_present}/{summary.vdb_total} files"
        print(
            f"{summary.source:<20} {summary.name:<20} {summary.num_samples:>8,} samples, "
            f"{summary.sequences:>5} seq, {state}, {human_bytes(summary.size_bytes)}"
        )
    return 0