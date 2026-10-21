"""``fogmgr download`` — pre-flight, estimate/guard, then the official tool."""

from __future__ import annotations

import argparse
import sys


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("download", help="download selected tars via the official tool")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preset", choices=("smoke", "medium", "full"))
    mode.add_argument("--percentage", metavar="P")
    mode.add_argument("--category", action="append", default=[], metavar="NAME")
    parser.add_argument("--max-samples", type=int, metavar="N")
    parser.add_argument("--include-bad", action="store_true")
    parser.add_argument("--revision", default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the estimate + exact command and stop")
    parser.add_argument("--force", action="store_true",
                        help="override the disk-space guard refusal")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace, client) -> int:
    if args.category and args.max_samples is None:
        print("error: --category requires --max-samples", file=sys.stderr)
        return 2
    if args.max_samples is not None and not args.category:
        print("error: --max-samples requires --category", file=sys.stderr)
        return 2
    if args.max_samples is not None and args.max_samples <= 0:
        print("error: --max-samples must be positive", file=sys.stderr)
        return 2

    options = dict(
        preset=args.preset,
        percentage=args.percentage,
        categories=args.category or None,
        max_samples=args.max_samples,
        include_bad=args.include_bad,
        revision=args.revision,
        force=args.force,
    )

    estimate = client.download_estimate(**options)
    if args.json and args.dry_run:
        from .common import emit_json

        emit_json(estimate)
        return 0

    _print_estimate(estimate)
    if args.dry_run:
        return 0
    if estimate.warning:
        print("WARNING:", estimate.warning)

    argv = client.build_official_argv(
        preset=args.preset,
        percentage=args.percentage,
        categories=tuple(args.category or ()),
        max_samples=args.max_samples,
        include_bad=args.include_bad,
        revision=args.revision or None,
    )
    print("starting official download (progress streams below)...")
    code = client.download(argv)
    if code != 0:
        from ..sources.vfxdb.official import EXIT_MEANINGS

        meaning = EXIT_MEANINGS.get(code, "unknown exit code")
        print(f"official tool exited {code}: {meaning}", file=sys.stderr)
    return code


def _print_estimate(estimate) -> None:
    from .common import human_bytes as hb

    print("VfxDB download estimate (FogManager replication of the official plan)")
    print(f"  source      : {estimate.source}")
    print(f"  mode        : {estimate.label}")
    print(f"  revision    : {estimate.revision}")
    print(f"  destination : {estimate.data_root}")
    print(f"  HF cache    : {estimate.cache_dir}")
    print(f"  tars        : {estimate.tar_count:,} / {estimate.available_tars:,}")
    print(
        f"  samples     : {estimate.normal_samples:,} normal "
        f"+ {estimate.bad_samples:,} io-bad in selected tars"
    )
    sizes = (
        f"  space       : ≈{hb(estimate.extracted_bytes)} extracted + "
        f"{hb(estimate.remote_bytes)} cache = {hb(estimate.needed_bytes)} needed"
    )
    if not estimate.remote_known:
        sizes += " (remote sizes estimated; exact listing unavailable)"
    print(sizes)
    print(f"  free        : {hb(estimate.free_bytes)}")
    for entry in estimate.per_category:
        print(
            f"    {entry['category']}: {entry['tars']} tar(s), "
            f"{entry['normal_samples']:,} normal / {entry['all_samples']:,} rows, "
            f"≈{hb(entry['extracted_bytes'])} extracted"
        )
    if estimate.command:
        print(f"  command     : {estimate.command}")