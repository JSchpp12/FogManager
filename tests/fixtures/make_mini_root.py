"""Generate fake VfxDB-shaped and local-import-shaped mini dataset roots.

Both fixtures build paths with pathlib only — no assumed separators or config
locations — and mirror the structures tests rely on:

- mini-dataset A (vfxdb kind): 2 categories with data + a partial sequence +
  an IO-bad row + ``EmptyCat`` (index only, zero samples) + ``.vfxdb/state.json``
  + ``meta/io_bad_vdbs.{jsonl,meta.json}``.
- mini-dataset B (local_import kind): same shape, no state.json, no io_bad
  files; also exercises repeat stems across sequences and a non-.vdb file.

Run as a script: ``python make_mini_root.py DEST`` prints the two roots.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

VDB_MAGIC = b"OPENVDB-MINI-FIXTURE\n"

REVISION = "0123456789abcdef0123456789abcdef01234567"


def _write_vdb(path: Path, size: int = 512) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = VDB_MAGIC * (size // len(VDB_MAGIC))
    payload += b"\0" * (size - len(payload))
    path.write_bytes(payload)


def _row(**overrides):
    row = {
        "id": None,
        "base_id": None,
        "seq": None,
        "folder": None,
        "vdb_path": None,
        "meta_path": None,
        "size_bytes": None,
        "mtime": 1762272020,
    }
    row.update(overrides)
    return row


def _write_category(category_dir: Path, category: str, rows: list[dict]) -> None:
    for row in rows:
        target = category_dir / row["vdb_path"]
        if not target.exists() and row["size_bytes"]:
            _write_vdb(target, row["size_bytes"])
    payload = {"category": category, "num_samples": len(rows), "samples": rows}
    (category_dir / "category_index.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def build_fake_vfxdb_root(root: Path) -> Path:
    """Mini-dataset A: vfxdb kind with all the extra control artifacts."""
    root = Path(root)
    alpha = root / "AlphaWave"
    rows_a = []
    # seq 10 spans tars "t0" only; complete.
    for i in range(2):
        rows_a.append(
            _row(id=f"s0010__n000{i}", base_id="s0010", seq=10, folder="t0",
                 vdb_path=f"t0/s0010__n000{i}.vdb", size_bytes=512,
                 meta_path=f"index/meta__s0010__n000{i}.json")
        )
    # seq 20: partial — one of two files present (folder t1).
    rows_a.append(_row(id="s0020__n0000", base_id="s0020", seq=20, folder="t1",
                       vdb_path="t1/s0020__n0000.vdb", size_bytes=512))
    rows_a.append(_row(id="s0020__n0001", base_id="s0020", seq=20, folder="t1",
                       vdb_path="t1/s0020__n0001.vdb", size_bytes=256))
    # IO-bad row inside tar t1: present on disk, flagged bad.
    rows_a.append(_row(id="s0020__n0002", base_id="s0020", seq=20, folder="t1",
                       vdb_path="t1/s0020__n0002.vdb", size_bytes=512))
    _write_category(alpha, "AlphaWave", rows_a)
    # Simulate the partial sequence: the writer populates every file, so
    # remove one member of seq 20 on purpose (index says 4, disk has 3).
    (alpha / "t1" / "s0020__n0001.vdb").unlink()
    for row in rows_a:
        if row["meta_path"]:
            meta_file = alpha / row["meta_path"]
            meta_file.parent.mkdir(parents=True, exist_ok=True)
            meta_file.write_text(json.dumps({"frame": row["id"]}) + "\n", encoding="utf-8")

    beta = root / "BetaFog"
    # beta uses the alternate vdb_path shape: single-part-per-level naming
    # with seq 0 (int) to exercise int seq_key.
    rows_b = [
        _row(id="s0000__n0000", base_id="s0000", seq=0, folder="t0",
             vdb_path="t0/s0000__n0000.vdb", size_bytes=512),
    ]
    _write_category(beta, "BetaFog", rows_b)

    # Index-only category: num_samples 0, no data dirs.
    empty = root / "EmptyCat"
    empty.mkdir(parents=True, exist_ok=True)
    (empty / "category_index.json").write_text(
        json.dumps({"category": "EmptyCat", "num_samples": 0, "samples": []}) + "\n",
        encoding="utf-8",
    )

    # Optional vfxdb-only artifacts: revision pin + io-bad catalog.
    control = root / ".vfxdb"
    control.mkdir(parents=True, exist_ok=True)
    (control / "state.json").write_text(
        json.dumps({"schema_version": 1, "repo_id": "ryogishiki/VfxDB",
                    "revision": REVISION, "archives": {}}) + "\n",
        encoding="utf-8",
    )
    meta_dir = root / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    bad_manifest = [
        {"category": "AlphaWave", "sequence": "t1",
         "archive_path": "archives/AlphaWave/t1.tar",
         "source_relpath": "AlphaWave/t1/s0020__n0002.vdb",
         "member_path": "s0020__n0002.vdb", "sample_key": "s0020__n0002"}
    ]
    (meta_dir / "io_bad_vdbs.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in bad_manifest), encoding="utf-8"
    )
    (meta_dir / "io_bad_vdbs.meta.json").write_text(
        json.dumps({"schema_version": 1, "record_count": len(bad_manifest),
                    "manifest_sha256": None}) + "\n",
        encoding="utf-8",
    )
    return root


def build_local_root(root: Path) -> Path:
    """Mini-dataset B: local_import kind — no state.json, no io_bad files."""
    root = Path(root)
    for seq, per_seq in (("seqA", ("a_one", "a_two")), ("seqB", ("b_one",))):
        for stem in per_seq:
            _write_vdb(root / "GammaMist" / seq / f"{stem}.vdb", 480)
    # Repeated stem across sequences (forces <seq>/<stem> id synthesis) +
    # a non-.vdb file that must be skipped.
    _write_vdb(root / "GammaMist" / "seqB" / "a_two.vdb", 480)
    (root / "GammaMist" / "seqB" / "notes.txt").write_text("not a vdb\n", encoding="utf-8")
    delta = root / "DeltaHaze"
    _write_vdb(delta / "runX" / "d001.vdb", 480)
    return root


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python make_mini_root.py DEST", file=sys.stderr)
        return 2
    base = Path(argv[1])
    a = build_fake_vfxdb_root(base / "vfxdb_root")
    b = build_local_root(base / "local_root")
    print(a)
    print(b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))