"""The VfxDB source adapter (kind ``vfxdb_hf``).

Wraps the Hugging Face dataset and its official downloader. All VfxDB-specific
knowledge lives in this package: official tool invocation, tar selection
estimates, space guarding, revision pinning, the io-bad catalog, and the HF
cache. Core never imports any of this.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core import catalog
from ...core.catalog import CategoryIndex
from ...core.config import Config, VfxdbSourceConfig, VfxdbToolState
from ...core.model import DownloadEstimate, SourceInfo
from ...errors import ConfigError, SourceNotFoundError, ToolError, UnknownCategoryError
from ..base import DownloadCapableSource
from . import official, selection, space


@dataclass
class CacheInfo:
    cache_dir: str
    exists: bool
    bytes_used: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "cache_dir": self.cache_dir,
            "exists": self.exists,
            "bytes_used": self.bytes_used,
            "human": space.human_bytes(self.bytes_used),
            "note": (
                "cache is the authoritative blob store; deleting it forfeits "
                "resume/reuse of already downloaded tars"
            ),
        }


class VfxdbSource(DownloadCapableSource):
    name = "vfxdb"
    kind = "vfxdb_hf"
    can_download = True

    def __init__(self, config: Config, entry: VfxdbSourceConfig) -> None:
        self._config = config
        self.entry = entry
        self.name = entry.name
        self.data_root = config.require_data_root(entry)
        self._cache_dir = config.require_cache_dir(entry)

    # -- construction ---------------------------------------------------------

    @classmethod
    def from_config(cls, config: Config, entry: Any) -> "VfxdbSource":
        if not isinstance(entry, VfxdbSourceConfig):  # pragma: no cover - registry guards
            raise ConfigError("vfxdb source requires a vfxdb_hf entry")
        return cls(config, entry)

    # -- Source protocol ------------------------------------------------------

    def list_categories(self) -> list[str]:
        return catalog.list_category_dirs(self.data_root)

    def read_index(self, category: str) -> CategoryIndex:
        index = catalog.read_category_index(self.category_dir(category))
        if index.category != category and index.name == category:
            # Category identity mismatch is fine to tolerate on read; readers
            # are defensive. Nothing to do here.
            pass
        return index

    def indexes(self, categories: list[str] | None = None) -> dict[str, CategoryIndex]:
        names = categories if categories is not None else self.list_categories()
        return {name: self.read_index(name) for name in names}

    # -- vfxdb-specific state -------------------------------------------------

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def pinned_revision(self) -> str | None:
        return catalog.pinned_revision(self.data_root)

    def io_bad_sources(self) -> set[str] | None:
        return catalog.read_io_bad_sources(self.data_root)

    # -- tool lifecycle -------------------------------------------------------

    def tool_path(self) -> Path | None:
        path = self.entry.tool.path
        return Path(path) if path else None

    def ensure_tool(self) -> Path:
        path = self.tool_path()
        if path is None or not path.is_file():
            raise ToolError("official downloader not fetched; run 'fogmgr fetch-tool' first")
        return path

    def fetch_tool(self, *, revision: str | None = None, refresh: bool = False) -> official.ToolState:
        state = official.fetch_tool(
            tools_dir=(
                Path(self.entry.tool.path).parent
                if self.entry.tool.path
                else official.default_tools_dir()
            ),
            cache_dir=self._cache_dir,
            revision=revision or self.entry.revision,
            refresh=refresh,
        )
        # Provenance is recorded whenever we know which tool we would run —
        # including a no-change fetch — so later commands resolve it.
        old_tool = self.entry.tool
        new_tool = VfxdbToolState(
            path=str(state.tool_path),
            sha256s=state.sha256s,
            fetched_revision=state.fetched_revision or old_tool.fetched_revision,
            fetched_at=state.fetched_at or old_tool.fetched_at,
        )
        self.entry.tool = new_tool
        if state.changed or old_tool.to_dict() != new_tool.to_dict():
            self._config.save()
        return state

    # -- official invocation --------------------------------------------------

    def resolve_revision(self, requested: str | None = None) -> str:
        """Effective revision for a download: requested > config > pinned > main."""
        return (
            requested
            or self.entry.revision
            or self.pinned_revision()
            or official.DEFAULT_REVISION
        )

    def install_metadata(self, *, include_bad: bool = False, revision: str | None = None,
                         dry_run: bool = False) -> tuple[int, str]:
        """Bare invocation = metadata-only install."""
        from ...errors import OfficialToolError

        argv = official.build_argv(
            self.data_root,
            self._cache_dir,
            include_bad=include_bad,
            revision=revision or self.entry.revision,
            tool_path=self.ensure_tool(),
        )
        command = official.display_command(argv)
        if dry_run:
            return 0, command
        code = official.run_official(argv)
        if code != 0:
            raise OfficialToolError(
                f"metadata install failed: official tool exited {code} "
                f"({official.EXIT_MEANINGS.get(code, 'unknown code')})"
            )
        return code, command

    def download(self, argv: list[str]) -> int:
        return official.run_official(argv)

    # -- estimation -----------------------------------------------------------

    def estimate(
        self,
        *,
        preset: str | None = None,
        percentage: str | None = None,
        categories: list[str] | None = None,
        max_samples: int | None = None,
        include_bad: bool = False,
        revision: str | None = None,
        force: bool = False,
    ) -> DownloadEstimate:
        """Replicate the official plan, aggregate bytes, run the disk guard."""
        effective_revision = self.resolve_revision(revision)
        installed = self.list_categories()
        io_bad = self.io_bad_sources() or set()

        requested = list(categories or [])
        if requested:
            unknown = [name for name in requested if name not in installed]
            if unknown:
                raise UnknownCategoryError(
                    f"unknown categories: {unknown}; install metadata first "
                    "(fogmgr install-metadata) or check spelling"
                )
        indexes = self.indexes(requested or None)
        plan = selection.select_tars(
            indexes,
            io_bad,
            preset=preset,
            percentage=percentage,
            categories=requested,
            max_samples=max_samples,
        )

        tar_paths = [
            f"archives/{tar.category}/{tar.folder}.tar"
            for cat in sorted(set(t.category for t in plan.tars))
            for tar in plan.tars if tar.category == cat
        ]
        # Include the meta tar in the remote listing (metadata installs and
        # every download mode pull it once).
        remote = space.fetch_remote_sizes(
            tar_paths + ["meta/vfxdb_meta.tar.zst"],
            effective_revision,
            official.REPO_ID,
        )
        meta_tar = remote.sizes.get("meta/vfxdb_meta.tar.zst")

        parts = space.estimate_parts(
            extracted_bytes=plan.extracted_bytes(),
            all_rows=plan.sum_rows(),
            selected_tar_paths=tar_paths,
        )
        needed = space.needed_bytes(parts, remote, meta_tar)
        warning = space.check_disk(needed, self.data_root, force=force)

        # A source pinned elsewhere is a hard conflict for the official tool;
        # warn early (the tool itself hard-rejects).
        pinned = self.pinned_revision()
        if pinned and effective_revision not in (pinned, official.DEFAULT_REVISION):
            warning = (warning + "\n" if warning else "") + (
                f"WARNING: data root is pinned to revision {pinned} but you requested "
                f"{effective_revision}; the official tool will reject this. "
                "Use a fresh data root or drop the pin."
            )

        argv = official.build_argv(
            self.data_root,
            self._cache_dir,
            preset=preset,
            percentage=percentage,
            categories=requested,
            max_samples=max_samples,
            include_bad=include_bad,
            revision=effective_revision if effective_revision != official.DEFAULT_REVISION else None,
            tool_path=self.ensure_tool(),
        )
        return DownloadEstimate(
            source=self.name,
            label=plan.label,
            revision=effective_revision,
            data_root=str(self.data_root),
            cache_dir=str(self._cache_dir),
            tar_count=plan.tar_count,
            available_tars=plan.available_tars,
            normal_samples=plan.normal_total(io_bad),
            bad_samples=plan.bad_total(io_bad),
            extracted_bytes=plan.extracted_bytes(),
            remote_known=remote.known,
            remote_bytes=needed - plan.extracted_bytes(),
            free_bytes=space.free_bytes(self.data_root),
            needed_bytes=needed,
            ok=warning is None,
            warning=warning,
            command=official.display_command(argv),
            per_category=list(plan.per_category(io_bad).values()),
        )

    # -- cache ----------------------------------------------------------------

    def cache_info(self) -> CacheInfo:
        used, exists = space.cache_usage(self._cache_dir)
        return CacheInfo(
            cache_dir=str(self._cache_dir), exists=exists, bytes_used=used
        )

    # -- misc -----------------------------------------------------------------

    def info(self) -> SourceInfo:
        return SourceInfo(
            name=self.name,
            kind=self.kind,
            can_download=self.can_download,
            data_root=str(self.data_root),
        )


def require_vfxdb(registry: Any, source: str | None = None) -> VfxdbSource:
    """Resolve the single (or named) vfxdb-kind source from a registry."""
    if source is None:
        vfxdb_sources = [
            s for s in registry.usable.values() if isinstance(s, VfxdbSource)
        ]
        if len(vfxdb_sources) == 1:
            return vfxdb_sources[0]
        if not vfxdb_sources:
            raise SourceNotFoundError(
                "no vfxdb sources registered; run 'fogmgr init' first"
            )
        names = ", ".join(sorted(s.name for s in vfxdb_sources))
        raise ConfigError(
            f"multiple vfxdb sources registered ({names}); pass --source"
        )
    try:
        candidate = registry.get(source)
    except KeyError:
        raise SourceNotFoundError(f"no usable source named {source!r}") from None
    if not isinstance(candidate, VfxdbSource):
        raise ConfigError(
            f"command requires a vfxdb source; source {candidate.name!r} is {candidate.kind!r}"
        )
    return candidate