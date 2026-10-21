"""``fogmgr fetch-tool`` — fetch/refresh the official bootstrap."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "fetch-tool", help="fetch the official VfxDB downloader bootstrap"
    )
    parser.add_argument("--revision", default=None, help="pin the fetch to a revision")
    parser.add_argument("--refresh", action="store_true",
                        help="re-fetch even if the tool is already present")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace, client) -> int:
    state = client.fetch_tool(revision=args.revision, refresh=args.refresh)
    print(f"tool: {state['path']}")
    if state["changed"]:
        print(f"fetched revision: {state['fetched_revision'] or 'main (bootstrap default)'}")
        for name, digest in sorted(state["sha256s"].items()):
            print(f"  sha256 {digest}  {name}")
        print(
            "note: the bootstrap fetches its sibling files from 'main' regardless of\n"
            "      revision, so sibling hashes are best-effort and this fetch is not\n"
            "      fully pin-reproducible."
        )
    else:
        print("already present; pass --refresh to re-fetch")
    return 0