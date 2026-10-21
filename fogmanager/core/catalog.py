"""Layout-generic readers for the VfxDB-shaped on-disk catalog.

Both the official VfxDB install and imported data use the same shape::

    <root>/<Category>/category_index.json     ({"category", "num_samples", "samples"})
    <root>/<Category>/<vdb_path>              (vdb_path is relative to the category dir)
    <root>/.vfxdb/state.json                  (vfxdb only — optional everywhere)
    <root>/meta/io_bad_vdbs.{jsonl,meta.json} (vfxdb only — optional everywhere)

Everything here is *defensive*: unknown fields are ignored, missing optional
files are tolerated, and readers raise :class:`CatalogError` on malformed
required content. VDB files themselves are never opened.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import CatalogError

INDEX_FILENAME = "category_index.json"
STATE_DIRNAME = ".vfxdb"
STATE_FILENAME = "state.json"
IO_BAD_MANIFEST = "meta/io_bad_vdbs.jsonl"
IO_BAD_INFO = "meta/io_bad_vdbs.meta.json"
SAMPLE_META_DIRNAME = "index"


@dataclass
class SampleRow:
    """One row of ``category_index.json`` — the canonical import schema.

    ``seq`` is kept exactly as found (int for VfxDB, string for imports).
    """

    id: str | None
    base_id: str | None
    seq: Any
    folder: str | None
    vdb_path: str
    meta_path: str | None
    size_bytes: int | None
    mtime: Any
    row_index: int = -1

    @property
    def seq_key(self) -> str:
        """Stable string form of the sequence for grouping/display."""
        return str(self.seq)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "base_id": self.base_id,
            "seq": self.seq,
            "folder": self.folder,
            "vdb_path": self.vdb_path,
            "meta_path": self.meta_path,
            "size_bytes": self.size_bytes,
            "mtime": self.mtime,
        }


@dataclass
class CategoryIndex:
    name: str
    category: str
    num_samples: int
    rows: list[SampleRow]
    path: Path


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise CatalogError(f"cannot read {path}: {exc}") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogError(f"{path} must contain a JSON object")
    return value


def read_category_index(category_dir: Path) -> CategoryIndex:
    """Read ``<category_dir>/category_index.json`` defensively."""
    path = Path(category_dir) / INDEX_FILENAME
    try:
        value = read_json_object(path)
    except FileNotFoundError:
        raise CatalogError(f"no {INDEX_FILENAME} in {category_dir}") from None
    rows_raw = value.get("samples")
    if not isinstance(rows_raw, list):
        raise CatalogError(f"{path}: 'samples' must be a list")
    rows: list[SampleRow] = []
    for i, raw in enumerate(rows_raw):
        if not isinstance(raw, dict):
            raise CatalogError(f"{path}: row {i} is not an object")
        vdb_path = raw.get("vdb_path")
        if not isinstance(vdb_path, str) or not vdb_path:
            raise CatalogError(f"{path}: row {i} has no 'vdb_path'")
        rows.append(
            SampleRow(
                id=_opt_str(raw.get("id")),
                base_id=_opt_str(raw.get("base_id")),
                seq=raw.get("seq"),
                folder=_opt_str(raw.get("folder")),
                vdb_path=vdb_path,
                meta_path=_opt_str(raw.get("meta_path")),
                size_bytes=_opt_int(raw.get("size_bytes")),
                mtime=raw.get("mtime"),
                row_index=i,
            )
        )
    num = value.get("num_samples")
    if not isinstance(num, int):
        num = len(rows)
    category = value.get("category")
    if not isinstance(category, str) or not category:
        category = Path(category_dir).name
    return CategoryIndex(
        name=Path(category_dir).name,
        category=category,
        num_samples=num,
        rows=rows,
        path=path,
    )


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _opt_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def sample_disk_path(category_dir: Path, row: SampleRow) -> Path:
    return Path(category_dir) / Path(row.vdb_path)


def sample_key(category: str, row: SampleRow) -> str:
    """The key used in the io-bad manifest: ``Category/<vdb_path>``."""
    return f"{category}/{row.vdb_path}"


def read_sample_meta(category_dir: Path, meta_path: str | None) -> dict[str, Any]:
    """Read the per-sample meta JSON (schema unpublished — raw dump)."""
    if not meta_path:
        raise CatalogError("sample has no meta_path")
    path = Path(category_dir) / meta_path
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CatalogError(f"sample meta not found: {path}") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"cannot read sample meta {path}: {exc}") from exc
    if not isinstance(value, dict):
        return {"value": value}
    return value


def read_state(data_root: Path) -> dict[str, Any] | None:
    """Read the official tool's revision pin (``.vfxdb/state.json``), if present.

    Never written by us — the official tool owns it.
    """
    path = Path(data_root) / STATE_DIRNAME / STATE_FILENAME
    if not path.is_file():
        return None
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"cannot read downloader state {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogError(f"downloader state {path} must be a JSON object")
    return value


def pinned_revision(data_root: Path) -> str | None:
    state = read_state(data_root)
    if state is None:
        return None
    revision = state.get("revision")
    return str(revision) if revision else None


def _validate_io_bad_paths(data_root: Path) -> tuple[Path, Path]:
    root = Path(data_root)
    return root / IO_BAD_MANIFEST, root / IO_BAD_INFO


def read_io_bad_sources(data_root: Path) -> set[str] | None:
    """Return the set of ``Category/<vdb_path>`` keys flagged IO-bad, or None.

    The manifest records ``source_relpath`` as ``Category/<folder>/<file>.vdb``
    (3 posix parts), which matches ``sample_key`` exactly.
    """
    manifest, info = _validate_io_bad_paths(data_root)
    if not manifest.is_file() or not info.is_file():
        return None
    try:
        text = manifest.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogError(f"cannot read {manifest}: {exc}") from exc
    sources: set[str] = set()
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CatalogError(f"{manifest} line {line_no}: {exc}") from exc
        if not isinstance(row, dict):
            raise CatalogError(f"{manifest} line {line_no}: not an object")
        rel = row.get("source_relpath")
        if not isinstance(rel, str) or rel.count("/") != 2 or not rel.endswith(".vdb"):
            raise CatalogError(f"{manifest} line {line_no}: bad source_relpath {rel!r}")
        sources.add(rel)
    return sources


def list_category_dirs(root: Path, *, skip: tuple[str, ...] = ("archives", "meta")) -> list[str]:
    """Categories present on disk: subdirs holding a ``category_index.json``."""
    root = Path(root)
    if not root.is_dir():
        return []
    names = [
        entry.name
        for entry in os.scandir(root)
        if entry.is_dir()
        and not entry.name.startswith(".")
        and entry.name not in skip
        and (Path(entry.path) / INDEX_FILENAME).is_file()
    ]
    return sorted(names)


def scan_category_disk(category_dir: Path) -> dict[str, int]:
    """One ``os.scandir`` walk of a category dir (``index/`` skipped).

    Returns ``{relative posix path: size_bytes}`` for every ``*.vdb`` file on
    disk — the on-disk reality, independent of the index.
    """
    category_dir = Path(category_dir)
    found: dict[str, int] = {}
    if not category_dir.is_dir():
        return found
    stack = [category_dir]
    while stack:
        current = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                if entry.name == SAMPLE_META_DIRNAME and entry.is_dir():
                    continue  # per-sample JSON sidecars live here
                if entry.is_dir():
                    stack.append(Path(entry.path))
                elif entry.name.lower().endswith(".vdb"):
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        size = 0
                    found[Path(entry.path).relative_to(category_dir).as_posix()] = size
    return found