"""Source registry: build live Source objects from the config registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.config import KIND_LOCAL, KIND_VFXDB, Config, UnknownSourceConfig
from ..core.model import SourceInfo
from .base import DownloadCapableSource, Source


@dataclass
class Registry:
    """Usable sources plus any entries whose kind this version can't drive."""

    usable: dict[str, Source] = field(default_factory=dict)
    unknown: dict[str, UnknownSourceConfig] = field(default_factory=dict)

    def get(self, name: str | None) -> Source:
        if name is None:
            if not self.usable:
                raise KeyError(name)
            if len(self.usable) > 1:
                names = ", ".join(sorted(self.usable))
                raise ValueError(f"multiple sources registered ({names}); pass --source")
            return next(iter(self.usable.values()))
        if name not in self.usable:
            raise KeyError(name)
        return self.usable[name]

    def require(self, name: str | None, *kinds: type) -> Source:
        source = self.get(name)
        if kinds and not isinstance(source, kinds):
            raise TypeError(source)
        return source

    def select(self, name: str | None = None) -> list[Source]:
        """All usable sources, or the single named one."""
        if name is None:
            return sorted(self.usable.values(), key=lambda s: s.name)
        return [self.get(name)]

    def infos(self) -> list[SourceInfo]:
        entries = [
            SourceInfo(
                name=s.name,
                kind=s.kind,
                can_download=s.can_download,
                data_root=str(s.data_root) if s.data_root else None,
            )
            for s in sorted(self.usable.values(), key=lambda x: x.name)
        ]
        entries.extend(
            SourceInfo(
                name=name,
                kind=entry.kind,
                can_download=False,
                data_root=str(entry.raw.get("data_root", "")) or None,
                usable=False,
                detail="unknown kind for this FogManager version; upgrade to use it",
            )
            for name, entry in sorted(self.unknown.items())
        )
        return entries


def build_registry(config: Config) -> Registry:
    """Instantiate Source adapters from config entries.

    Unknown kinds stay listed (forward compatibility) but raise on use.
    """

    registry = Registry()
    from .local import LocalSource  # local import keeps module graph shallow
    from .vfxdb.source import VfxdbSource

    builders: dict[str, Any] = {KIND_VFXDB: VfxdbSource, KIND_LOCAL: LocalSource}
    for name, entry in config.sources.items():
        builder = builders.get(getattr(entry, "kind", ""))
        if builder is None:
            registry.unknown[name] = entry  # type: ignore[assignment]
            continue
        source = builder.from_config(config, entry)
        registry.usable[name] = source
    return registry


__all__ = [
    "Source",
    "DownloadCapableSource",
    "Registry",
    "build_registry",
]