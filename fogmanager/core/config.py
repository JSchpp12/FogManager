"""Configuration: platformdirs resolution + the source registry.

Machine paths never belong in the repo — everything lives in the config file.
Resolution order: explicit ``--config PATH`` > ``FOGMGR_CONFIG`` env var >
``platformdirs.user_config_dir("FogManager")/config.json``.

Schema (v1)::

    {
      "schema_version": 1,
      "sources": {
        "vfxdb": {"kind": "vfxdb_hf", "data_root": "...", "cache_dir": "...",
                  "revision": null, "tool": {"path": ..., "sha256s": {}, ...}},
        "<NAME>": {"kind": "local_import", "data_root": "...",
                   "index_dir": null, "imported_at": "..."}
      }
    }

Unknown ``kind`` values are preserved (forward compatibility) and reported as
such by ``fogmgr sources``; anything that tries to *use* them gets a clear
error.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union

import platformdirs

from ..errors import ConfigError, SourceNotFoundError

CONFIG_SCHEMA_VERSION = 1

KIND_VFXDB = "vfxdb_hf"
KIND_LOCAL = "local_import"

#: The canonical name of the default VfxDB source created by ``fogmgr init``.
DEFAULT_VFXDB_SOURCE = "vfxdb"


def default_config_path() -> Path:
    return (
        Path(platformdirs.user_config_dir("FogManager", appauthor=False))
        / "config.json"
    )


def resolve_config_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Order: explicit argument > ``FOGMGR_CONFIG`` env var > platform default."""
    if explicit is not None:
        return Path(explicit).expanduser()
    env = os.environ.get("FOGMGR_CONFIG")
    if env:
        return Path(env).expanduser()
    return default_config_path()


@dataclass
class VfxdbToolState:
    """Provenance of the fetched official bootstrap (see sources/vfxdb/official.py).

    Caveat recorded here and in the README: the bootstrap fetches its sibling
    implementation files from ``main`` regardless of revision, so sibling
    hashes are best-effort and the fetch is not fully pin-reproducible.
    """

    path: str | None = None
    sha256s: dict[str, str] = field(default_factory=dict)
    fetched_revision: str | None = None
    fetched_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256s": dict(self.sha256s),
            "fetched_revision": self.fetched_revision,
            "fetched_at": self.fetched_at,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "VfxdbToolState":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ConfigError("'tool' entry must be an object")
        sha256s = value.get("sha256s") or {}
        if not isinstance(sha256s, dict):
            raise ConfigError("'tool.sha256s' must be an object")
        return cls(
            path=value.get("path"),
            sha256s={str(k): str(v) for k, v in sha256s.items()},
            fetched_revision=value.get("fetched_revision"),
            fetched_at=value.get("fetched_at"),
        )


@dataclass
class VfxdbSourceConfig:
    name: str
    kind: str = KIND_VFXDB
    data_root: str | None = None
    cache_dir: str | None = None
    revision: str | None = None
    tool: VfxdbToolState = field(default_factory=VfxdbToolState)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "data_root": self.data_root,
            "cache_dir": self.cache_dir,
            "revision": self.revision,
            "tool": self.tool.to_dict(),
        }

    @classmethod
    def from_dict(cls, name: str, value: dict[str, Any]) -> "VfxdbSourceConfig":
        for key in ("data_root", "cache_dir"):
            if key in value and value[key] is not None and not isinstance(value[key], str):
                raise ConfigError(f"source {name!r}: {key!r} must be a string path")
        return cls(
            name=name,
            kind=KIND_VFXDB,
            data_root=value.get("data_root"),
            cache_dir=value.get("cache_dir"),
            revision=value.get("revision"),
            tool=VfxdbToolState.from_dict(value.get("tool")),
        )


@dataclass
class LocalImportSourceConfig:
    name: str
    kind: str = KIND_LOCAL
    data_root: str = ""
    index_dir: str | None = None
    imported_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "data_root": self.data_root,
            "index_dir": self.index_dir,
            "imported_at": self.imported_at,
        }

    @classmethod
    def from_dict(cls, name: str, value: dict[str, Any]) -> "LocalImportSourceConfig":
        data_root = value.get("data_root")
        if not isinstance(data_root, str) or not data_root:
            raise ConfigError(f"source {name!r}: local_import sources need 'data_root'")
        return cls(
            name=name,
            kind=KIND_LOCAL,
            data_root=data_root,
            index_dir=value.get("index_dir"),
            imported_at=value.get("imported_at"),
        )


@dataclass
class UnknownSourceConfig:
    """A registered source whose ``kind`` this version does not understand."""

    name: str
    kind: str
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.raw)


SourceConfig = Union[VfxdbSourceConfig, LocalImportSourceConfig, UnknownSourceConfig]


def _source_from_dict(name: str, value: Any) -> SourceConfig:
    if not isinstance(value, dict):
        raise ConfigError(f"source {name!r} must be an object")
    kind = value.get("kind")
    if kind == KIND_VFXDB:
        return VfxdbSourceConfig.from_dict(name, value)
    if kind == KIND_LOCAL:
        return LocalImportSourceConfig.from_dict(name, value)
    return UnknownSourceConfig(name=name, kind=str(kind), raw=value)


@dataclass
class Config:
    path: Path | None
    schema_version: int = CONFIG_SCHEMA_VERSION
    sources: dict[str, SourceConfig] = field(default_factory=dict)

    # -- accessors -----------------------------------------------------------

    def get(self, name: str) -> SourceConfig:
        try:
            return self.sources[name]
        except KeyError:
            raise _source_not_found(name) from None

    def find_vfxdb(self, name: str | None = None) -> VfxdbSourceConfig:
        """Resolve the VfxDB source: by name, or the single vfxdb-kind entry."""
        if name is not None:
            entry = self.sources.get(name)
            if entry is None:
                raise _source_not_found(name)
            if isinstance(entry, VfxdbSourceConfig):
                return entry
            raise ConfigError(
                f"source {name!r} has kind {entry.kind!r}, expected {KIND_VFXDB!r}"
            )
        matches = [
            entry
            for entry in self.sources.values()
            if isinstance(entry, VfxdbSourceConfig)
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise _source_not_found(DEFAULT_VFXDB_SOURCE, hint="run 'fogmgr init' first")
        names = ", ".join(sorted(m.name for m in matches))
        raise ConfigError(
            f"multiple vfxdb sources registered ({names}); pass --source to pick one"
        )

    def require_data_root(self, entry: VfxdbSourceConfig) -> Path:
        if not entry.data_root:
            raise ConfigError(
                f"source {entry.name!r} has no data_root; run 'fogmgr init' to set one"
            )
        return Path(entry.data_root)

    def require_cache_dir(self, entry: VfxdbSourceConfig) -> Path:
        if not entry.cache_dir:
            raise ConfigError(
                f"source {entry.name!r} has no cache_dir; run 'fogmgr init' to set one"
            )
        return Path(entry.cache_dir)

    # -- persistence ---------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sources": {name: entry.to_dict() for name, entry in self.sources.items()},
        }

    def save(self, path: Path | None = None) -> Path:
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ConfigError("cannot save: no config path resolved (pass --config)")
        target = Path(target)
        payload = json.dumps(self.to_dict(), indent=2) + "\n"
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".config-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp_name, target)
        except BaseException:
            with_temp = Path(tmp_name)
            if with_temp.exists():
                with_temp.unlink(missing_ok=True)
            raise
        return target

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> "Config":
        resolved = resolve_config_path(path)
        if not resolved.is_file():
            # Uninitialized install: usable, but vfxdb-aware commands must tell
            # the user to run 'fogmgr init'.
            return cls(path=resolved)
        try:
            raw = json.loads(resolved.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"config {resolved} is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"config {resolved} must be a JSON object")
        version = raw.get("schema_version", CONFIG_SCHEMA_VERSION)
        if version != CONFIG_SCHEMA_VERSION:
            raise ConfigError(
                f"config {resolved} has schema_version {version!r}; "
                f"this version understands {CONFIG_SCHEMA_VERSION}"
            )
        sources_raw = raw.get("sources", {})
        if not isinstance(sources_raw, dict):
            raise ConfigError(f"config {resolved}: 'sources' must be an object")
        sources = {name: _source_from_dict(name, value) for name, value in sources_raw.items()}
        return cls(path=resolved, schema_version=CONFIG_SCHEMA_VERSION, sources=sources)


def _source_not_found(name: str, hint: str | None = None) -> Exception:
    message = f"source {name!r} is not registered"
    if hint:
        message = f"{message}; {hint}"
    return SourceNotFoundError(message)