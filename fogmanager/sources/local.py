"""Local import source (kind ``local_import``).

The import contract (documented in the README): OpenVDB frames arranged as
``<Category>/<sequence>/<sample>.vdb`` under one root. Generation stays
external to FogManager — this module only ingests.

Import walks the tree, ``os.stat``s each ``.vdb`` file for real
``size_bytes``/``mtime``, and writes a ``category_index.json`` per touched
category in the VfxDB row schema so the shared catalog readers serve both
sources::

    {id, base_id, seq, folder, vdb_path, meta_path, size_bytes, mtime}

Row synthesis rules (documented in the README):
- ``seq`` / ``folder``: the sequence directory name (folder mirrors the
  VfxDB semantics of being ``vdb_path``'s first component).
- ``id``: the file stem; if stems repeat across sequences within a category,
  ``id`` becomes ``<seq>/<stem>`` so ids stay unique.
- ``base_id``: the stem (best-effort mirror of VfxDB semantics).
- ``vdb_path``: path relative to the category dir.
- ``meta_path``: ``index/<stem>.json`` when a sidecar exists, else null.
- ``size_bytes``/``mtime``: real filesystem values (mtime as int seconds).

Indexes are written next to the data by default; ``--index-dir`` relocates
them for read-only trees. Imported sources are static — no resume state, no
revision pinning, ``--re-index`` re-walks and rewrites.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core import catalog
from ..core.catalog import CategoryIndex, SampleRow
from ..core.config import KIND_LOCAL, Config, LocalImportSourceConfig
from ..core.model import ImportResult, SourceInfo
from ..errors import ConfigError, IndexExistsError
from .base import Source

INDEX_DIRNAME = catalog.SAMPLE_META_DIRNAME


class LocalSource(Source):
    kind = KIND_LOCAL
    can_download = False

    def __init__(self, config: Config, entry: LocalImportSourceConfig) -> None:
        self._config = config
        self.entry = entry
        self.name = entry.name
        self.data_root = Path(entry.data_root).expanduser()
        self._index_dir = Path(entry.index_dir).expanduser() if entry.index_dir else None

    @classmethod
    def from_config(cls, config: Config, entry: Any) -> "LocalSource":
        if not isinstance(entry, LocalImportSourceConfig):  # pragma: no cover
            raise ConfigError("local source requires a local_import entry")
        return cls(config, entry)

    # -- layout ---------------------------------------------------------------

    def index_dir(self) -> Path:
        return self._index_dir if self._index_dir is not None else self.data_root

    def index_path(self, category: str) -> Path:
        return self.index_dir() / category / catalog.INDEX_FILENAME

    def category_dir(self, category: str) -> Path:
        # Sample files always live under data_root; only indexes may relocate.
        return self.data_root / category

    # -- Source protocol ------------------------------------------------------

    def list_categories(self) -> list[str]:
        return catalog.list_category_dirs(self.index_dir())

    def read_index(self, category: str) -> CategoryIndex:
        index_dir = self.index_dir()
        path = self.index_path(category)
        index = catalog.read_category_index(index_dir / category)
        index.path = path
        return index

    def info(self) -> SourceInfo:
        return SourceInfo(
            name=self.name,
            kind=self.kind,
            can_download=False,
            data_root=str(self.data_root),
        )


# --------------------------------------------------------------------- import


def _scan_category(category_dir: Path) -> tuple[list[tuple[str, str, Path, os.stat_result]], int]:
    """Return ([(seq, stem, path, stat)...], skipped_file_count)."""
    found: list[tuple[str, str, Path, os.stat_result]] = []
    skipped = 0
    with os.scandir(category_dir) as top:
        seq_entries = [e for e in top if e.is_dir()]
    for seq_entry in sorted(seq_entries, key=lambda e: e.name):
        with os.scandir(seq_entry.path) as files:
            for file_entry in sorted(files, key=lambda e: e.name):
                if not file_entry.is_file():
                    continue
                if not file_entry.name.lower().endswith(".vdb"):
                    skipped += 1
                    continue
                path = Path(file_entry.path)
                st = path.stat()
                stem = path.stem
                found.append((seq_entry.name, stem, path, st))
    return found, skipped


def _synthesize_rows(
    category: str,
    category_dir: Path,
    files: list[tuple[str, str, Path, os.stat_result]],
) -> list[SampleRow]:
    """Synthesize VfxDB-schema rows; ids uniquely disambiguated on collisions."""
    stems: dict[str, str] = {}
    for seq, stem, _path, _st in files:
        previous = stems.setdefault(stem, seq)
        if previous != seq:
            stems[stem] = "/"  # sentinel: stems collide across sequences
    rows: list[SampleRow] = []
    for seq, stem, path, st in files:
        collision = stems.get(stem) == "/"
        sample_id = f"{seq}/{stem}" if collision else stem
        meta_rel: str | None = None
        sidecar = category_dir / INDEX_DIRNAME / f"{stem}.json"
        if sidecar.is_file():
            meta_rel = f"{INDEX_DIRNAME}/{sidecar.name}"
        rows.append(
            SampleRow(
                id=sample_id,
                base_id=stem,
                seq=seq,
                folder=seq,
                vdb_path=f"{seq}/{path.name}",
                meta_path=meta_rel,
                size_bytes=st.st_size,
                mtime=int(st.st_mtime),
            )
        )
    rows.sort(key=lambda r: (str(r.seq), str(r.id)))
    return rows


def build_import_index(
    root: Path,
    category: str,
    *,
    categories_filter: list[str] | None = None,
) -> tuple[list[SampleRow], int] | None:
    """Walk one category dir; returns rows + skipped count, or None if empty."""
    if categories_filter and category not in categories_filter:
        return None
    files, skipped = _scan_category(root / category)
    if not files:
        return None
    rows = _synthesize_rows(category, root / category, files)
    return rows, skipped


def import_tree(
    config: Config,
    *,
    name: str,
    root: str | os.PathLike[str],
    categories: list[str] | None = None,
    index_dir: str | os.PathLike[str] | None = None,
    re_index: bool = False,
) -> ImportResult:
    """Implement ``fogmgr import NAME PATH ...`` and register the source.

    ``--re-index`` re-walks an already-registered local_import source; without
    it, both a clashing source name and a pre-existing index file refuse.
    """
    source_root = Path(root).expanduser().resolve()
    if not source_root.is_dir():
        raise ConfigError(f"import root {source_root} is not a directory")
    if not name or "/" in name:
        raise ConfigError(f"invalid source name {name!r}")

    existing = config.sources.get(name)
    if existing is not None:
        if not isinstance(existing, LocalImportSourceConfig) or not re_index:
            raise ConfigError(
                f"source name {name!r} already registered; pick another name"
                + ("; pass --re-index to re-walk and rewrite its indexes" if isinstance(existing, LocalImportSourceConfig) else "")
            )
        # Re-index: keep the registered root unless a new one was supplied.
        if str(source_root) != existing.data_root and index_dir is None and categories is None:
            source_root = Path(existing.data_root)
        if index_dir is None:
            index_dir = existing.index_dir

    reloc = Path(index_dir).expanduser().resolve() if index_dir else None
    filter_names = categories or None
    subdir_names = sorted(
        entry.name
        for entry in os.scandir(source_root)
        if entry.is_dir() and not entry.name.startswith(".")
    )

    per_category: list[dict[str, Any]] = []
    total_samples = 0
    total_bytes = 0
    skipped_total = 0

    for category in subdir_names:
        built = build_import_index(source_root, category, categories_filter=filter_names)
        if built is None:
            if filter_names and category in filter_names:
                raise ConfigError(
                    f"category {category!r} contains no <sequence>/<sample>.vdb files"
                )
            continue
        rows, skipped = built
        dest_dir = (reloc if reloc is not None else source_root) / category
        if not re_index and (dest_dir / catalog.INDEX_FILENAME).is_file():
            raise IndexExistsError(
                f"{dest_dir / catalog.INDEX_FILENAME} exists; pass --re-index to rewrite"
            )
        payload = {
            "category": category,
            "num_samples": len(rows),
            "samples": [row.to_dict() for row in rows],
        }
        dest_dir.mkdir(parents=True, exist_ok=True)
        index_file = dest_dir / catalog.INDEX_FILENAME
        index_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        bytes_here = sum(r.size_bytes or 0 for r in rows)
        sequences = sorted({str(r.seq) for r in rows})
        per_category.append(
            {
                "category": category,
                "samples": len(rows),
                "sequences": len(sequences),
                "bytes": bytes_here,
                "index_path": str(index_file),
            }
        )
        total_samples += len(rows)
        total_bytes += bytes_here
        skipped_total += skipped

    if not per_category:
        raise ConfigError(
            f"no categories with <Category>/<sequence>/<sample>.vdb files found under {source_root}"
        )

    entry = LocalImportSourceConfig(
        name=name,
        kind=KIND_LOCAL,
        data_root=str(source_root),
        index_dir=str(reloc) if reloc is not None else None,
        imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    config.sources[name] = entry
    config.save()

    return ImportResult(
        source=name,
        root=str(source_root),
        index_dir=str(reloc) if reloc is not None else None,
        per_category=per_category,
        total_samples=total_samples,
        total_bytes=total_bytes,
        skipped_files=skipped_total,
    )