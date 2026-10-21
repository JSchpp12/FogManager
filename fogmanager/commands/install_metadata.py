"""``fogmgr install-metadata`` — bare official invocation = metadata-only install."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "install-metadata", help="install category indexes + control files (official tool)"
    )
    parser.add_argument("--include-bad", action="store_true",
                        help="retain IO-bad samples when later downloading")
    parser.add_argument("--revision", default=None, help="revision for the metadata install")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace, client) -> int:
    result = client.install_metadata(
        include_bad=args.include_bad, revision=args.revision
    )
    # run_official already streamed the tool's own output live.
    return result["exit_code"]