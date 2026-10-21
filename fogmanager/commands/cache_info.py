"""``fogmgr cache-info`` — HF cache usage for the vfxdb source."""

from __future__ import annotations

import argparse

from .common import human_bytes


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("cache-info", help="HF cache size and safety note")
    parser.add_argument("--source", metavar="NAME")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace, client) -> int:
    from .common import emit_json

    info = client.cache_info(source=args.source)
    if args.json:
        emit_json(info)
        return 0
    if not info["exists"]:
        print(f"cache dir {info['cache_dir']} does not exist yet (created on first download)")
        return 0
    print(f"cache dir : {info['cache_dir']}")
    print(f"usage     : {human_bytes(info['bytes_used'])} ({info['bytes_used']:,} bytes)")
    print("safety    : the cache is the authoritative blob store; deleting it")
    print("            forfeits resume/reuse of already downloaded tars.")
    print("            Space model: extracted data + cached blobs coexist (≈2× extracted).")
    return 0