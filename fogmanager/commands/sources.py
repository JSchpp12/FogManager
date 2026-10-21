"""``fogmgr sources`` — list registered sources and their kinds."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("sources", help="list registered sources")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace, client) -> int:
    from .common import emit_json

    infos = client.sources()
    if args.json:
        emit_json(infos)
        return 0
    if not infos:
        print("no sources registered; run 'fogmgr init' (vfxdb) or 'fogmgr import' (local)")
        return 0
    for info in infos:
        flags = []
        if info.can_download:
            flags.append("download-capable")
        if not info.usable:
            flags.append("unusable: " + (info.detail or "unknown kind"))
        suffix = f"  ({'; '.join(flags)})" if flags else ""
        print(f"{info.name:<20} {info.kind:<14} {info.data_root or '-'}{suffix}")
    return 0