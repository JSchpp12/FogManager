"""``fogmgr inspect`` — read-only JSON views; never opens VDBs or the network."""

from __future__ import annotations

import argparse
import json

from .common import human_bytes


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("inspect", help="inspect a category's index rows")
    parser.add_argument("category", metavar="CATEGORY")
    parser.add_argument("--sequence", metavar="NAME", help="restrict to one sequence")
    parser.add_argument("--sample", metavar="ID", help="render one sample + its meta JSON (raw dump)")
    parser.add_argument("--limit", type=int, default=10, metavar="N",
                        help="row list length (default 10)")
    parser.add_argument("--source", metavar="NAME")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace, client) -> int:
    source = args.source
    if args.sample is not None:
        sample = client.paths(args.sample, args.category, source)[0]
        meta = client.sample_meta(args.sample, args.category, sample.source)
        if args.json:
            from .common import emit_json

            emit_json({"sample": sample, "meta": meta})
            return 0
        print(f"sample {args.sample} (source {sample.source}, category {args.category})")
        print(f"  path      : {sample.path}")
        print(f"  present   : {sample.present}")
        print(f"  size      : {human_bytes(sample.size_bytes or 0)}")
        print(f"  sequence  : {sample.seq}  folder: {sample.folder}  base_id: {sample.base_id}")
        print("  meta JSON (schema unpublished — raw dump):")
        print(json.dumps(meta, indent=2, sort_keys=True))
        return 0

    totals = client.category_totals(args.category, source)
    if args.json:
        from .common import emit_json

        emit_json(
            {
                "totals": totals,
                "sequences": client.sequences(args.category, source),
                "samples": client.samples(args.category, args.sequence, source, args.limit),
            }
        )
        return 0

    print(f"category {args.category} (source {totals.source})")
    print(f"  samples   : {totals.num_samples:,}")
    print(f"  sequences : {len(totals.sequences)}")
    print(f"  tars/folders: {len(totals.folders)} ({', '.join(totals.folders[:8])}"
          + (", ..." if len(totals.folders) > 8 else "") + ")")
    print(f"  on disk   : {human_bytes(totals.size_bytes)}")
    print("  per-sequence rollups:")
    for seq in client.sequences(args.category, source):
        state = "complete" if seq.complete else f"{seq.vdb_present}/{seq.vdb_total}"
        print(
            f"    {seq.name:<12} {seq.num_samples:>6,} samples  {state:<12} "
            f"{human_bytes(seq.size_bytes)}"
        )
    print(f"  rows (limit {args.limit}):")
    for sample in client.samples(args.category, args.sequence, source, args.limit):
        marker = "✓" if sample.present else "✗"
        print(f"    {marker} {sample.vdb_path}  seq={sample.seq}  "
              f"{human_bytes(sample.size_bytes or 0)}")
    return 0