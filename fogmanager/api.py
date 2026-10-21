"""STABLE public Python API.

Semver from day one; breaking changes bump the major version. The CLI is a
thin wrapper over this module — never the other way around. Other
applications depend on this surface:

    from fogmanager.api import Client

    client = Client.load()                       # default config resolution
    client.sources()                             # -> [SourceInfo]
    client.categories(source=None)               # -> [CategorySummary]
    client.sequences("RisingFlame")              # -> [SequenceSummary]
    client.samples("RisingFlame", limit=20)      # -> [Sample]
    client.status()                              # -> StatusReport
    client.paths("s0000__n0075", "RisingFlame")  # -> [Sample]

Errors are FogManagerError subclasses (same module) — catching them is part
of the contract. Raising argparse/subprocess internals through this API is
forbidden; download orchestration surfaces only estimates and exit codes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core.config import Config
from .core.model import (  # noqa: F401 - re-exported as the stable contract
    CatalogTotals,
    CategoryStatus,
    CategorySummary,
    DownloadEstimate,
    ImportResult,
    Sample,
    SequenceSummary,
    SourceInfo,
    SourceStatus,
    StatusReport,
)
from .core import queries
from .errors import (  # noqa: F401 - re-exported error hierarchy
    CatalogError,
    ConfigError,
    DependencyError,
    FogManagerError,
    IndexExistsError,
    OfficialToolError,
    SourceNotFoundError,
    SpaceGuardError,
    ToolError,
    UnknownCategoryError,
    UnknownSampleError,
)
from .sources import Registry, build_registry
from .sources.base import Source

API_VERSION = "1.0.0"


class Client:
    """Entry point for the FogManager library API."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._registry: Registry | None = None

    # -- construction ---------------------------------------------------------

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "Client":
        """Load config (None = default resolution: --config > env > platformdirs)."""
        return cls(Config.load(config_path))

    @property
    def config(self) -> Config:
        return self._config

    def registry(self) -> Registry:
        if self._registry is None:
            self._registry = build_registry(self._config)
        return self._registry

    def _sources(self, source: str | None) -> list[Source]:
        registry = self.registry()
        if source is None:
            return registry.select(None)
        try:
            return registry.select(source)
        except KeyError:
            raise SourceNotFoundError(f"no usable source named {source!r}") from None

    def _for_category(self, category: str, source: str | None) -> list[Source]:
        """Sources holding an index for ``category`` (merged when source=None)."""
        candidates = self._sources(source)
        if not candidates:
            raise SourceNotFoundError("no usable sources registered; run 'fogmgr init' first")
        if source is not None:
            return candidates
        found = [s for s in candidates if category in s.list_categories()]
        if not found:
            raise UnknownCategoryError(
                f"category {category!r} not found in any registered source"
            )
        return found

    def _one_for_category(self, category: str, source: str | None) -> Source:
        """Single source for single-target ops; refuses genuine ambiguity."""
        found = self._for_category(category, source)
        if len(found) > 1:
            names = ", ".join(sorted(s.name for s in found))
            raise ConfigError(
                f"category {category!r} exists in sources ({names}); pass --source"
            )
        return found[0]

    # -- read API -------------------------------------------------------------

    def sources(self) -> list[SourceInfo]:
        """All registered sources, including kinds this version can't drive."""
        return self.registry().infos()

    def categories(self, source: str | None = None) -> list[CategorySummary]:
        """Merged category summaries; each tagged with its source name."""
        return queries.list_categories(self._sources(source))

    def sequences(self, category: str, source: str | None = None) -> list[SequenceSummary]:
        return [
            summary
            for src in self._for_category(category, source)
            for summary in queries.list_sequences(src, category)
        ]

    def samples(
        self,
        category: str,
        sequence: str | None = None,
        source: str | None = None,
        limit: int | None = None,
    ) -> list[Sample]:
        out: list[Sample] = []
        for src in self._for_category(category, source):
            remaining = None if limit is None else max(0, limit - len(out))
            out.extend(
                queries.list_samples(src, category, sequence=sequence, limit=remaining)
            )
            if limit is not None and len(out) >= limit:
                break
        return out

    def status(self, source: str | None = None, category: str | None = None) -> StatusReport:
        return queries.build_status(self._sources(source), category)

    def paths(self, sample_id: str, category: str, source: str | None = None) -> list[Sample]:
        """Filesystem path(s) for a sample id — via the returned Sample objects."""
        return queries.resolve_sample_paths(
            self._one_for_category(category, source), category, sample_id
        )

    def sample_meta(
        self, sample_id: str, category: str, source: str | None = None
    ) -> dict[str, Any]:
        """Raw per-sample meta JSON (schema unpublished — raw dump)."""
        return queries.read_sample_meta(
            self._one_for_category(category, source), category, sample_id
        )

    def category_totals(self, category: str, source: str | None = None) -> CatalogTotals:
        return queries.category_totals(self._one_for_category(category, source), category)

    # -- import ---------------------------------------------------------------

    def import_source(
        self,
        name: str,
        path: str | Path,
        *,
        categories: list[str] | None = None,
        index_dir: str | Path | None = None,
        re_index: bool = False,
    ) -> ImportResult:
        from .sources.local import import_tree

        return import_tree(
            self._config,
            name=name,
            root=path,
            categories=categories,
            index_dir=index_dir,
            re_index=re_index,
        )

    # -- vfxdb orchestration --------------------------------------------------

    def _vfxdb(self, source: str | None = None):
        from .sources.vfxdb.source import VfxdbSource, require_vfxdb

        return require_vfxdb(self.registry(), source)

    def fetch_tool(
        self, *, revision: str | None = None, refresh: bool = False, source: str | None = None
    ) -> dict[str, Any]:
        state = self._vfxdb(source).fetch_tool(revision=revision, refresh=refresh)
        return {
            "path": str(state.tool_path),
            "sha256s": dict(state.sha256s),
            "fetched_revision": state.fetched_revision,
            "fetched_at": state.fetched_at,
            "changed": state.changed,
        }

    def install_metadata(
        self,
        *,
        include_bad: bool = False,
        revision: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        source_obj = self._vfxdb(source)
        code, command = source_obj.install_metadata(include_bad=include_bad, revision=revision)
        return {"exit_code": code, "command": command}

    def download_estimate(
        self,
        *,
        preset: str | None = None,
        percentage: str | None = None,
        categories: list[str] | None = None,
        max_samples: int | None = None,
        include_bad: bool = False,
        revision: str | None = None,
        force: bool = False,
        source: str | None = None,
    ) -> DownloadEstimate:
        return self._vfxdb(source).estimate(
            preset=preset,
            percentage=percentage,
            categories=categories,
            max_samples=max_samples,
            include_bad=include_bad,
            revision=revision,
            force=force,
        )

    def download(
        self,
        argv: list[str],
        source: str | None = None,
    ) -> int:
        """Run the official tool with exactly ``argv``. Returns its exit code.

        Callers must build argv via build_official_argv; the estimate/dry-run
        path is download_estimate.
        """
        return self._vfxdb(source).download(argv)

    def build_official_argv(self, estimate_command: str | None = None, **options: Any) -> list[str]:
        """Public hook to construct official argv from the same options as estimate."""
        from .sources.vfxdb.official import build_argv

        source_obj = self._vfxdb(options.pop("source", None))
        return build_argv(
            source_obj.data_root,
            source_obj.cache_dir,
            tool_path=source_obj.ensure_tool(),
            **options,
        )

    def cache_info(self, source: str | None = None) -> dict[str, Any]:
        return self._vfxdb(source).cache_info().to_dict()