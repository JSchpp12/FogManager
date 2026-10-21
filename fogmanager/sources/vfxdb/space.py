"""Space math for VfxDB downloads (vfxdb source only).

Space model: extracted data and the cached tar blobs coexist on the target
filesystem, so the guard uses ``needed ≈ extracted + remote(cache)`` and
refuses when ``needed + margin`` exceeds free space. Our checks use
``shutil.disk_usage`` (portable); the official tool re-checks too.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from ...errors import SpaceGuardError

#: Hard guard margin (mirrors the original plan's 1 GiB safety margin).
GUARD_MARGIN_BYTES = 1024 * 1024 * 1024
#: Warn when the estimate would consume more than this fraction of free space.
WARN_FRACTION = 0.8
#: Fallback bytes per tar member when exact tar sizes are unavailable.
FALLBACK_MEMBER_OVERHEAD = 1024
#: meta/vfxdb_meta.tar.zst fallback size when the remote listing is unavailable.
FALLBACK_META_TAR_BYTES = 74_000_000

GET_PATHS_INFO_BATCH = 200


def human_bytes(value: float | int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if size < 1024.0 or unit == "PiB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    raise AssertionError("unreachable")


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(Path(path)).free


@dataclass
class RemoteSizes:
    sizes: dict[str, int]
    known: bool


def fetch_remote_sizes(
    paths: list[str],
    revision: str | None,
    repo_id: str,
) -> RemoteSizes:
    """Exact remote tar sizes via ``get_paths_info``, batched; {} if it fails.

    ``known=False`` signals the caller to use the documented fallback
    (extracted bytes + 1 KiB/member).
    """
    if not paths:
        return RemoteSizes(sizes={}, known=True)
    try:
        from huggingface_hub import HfApi
    except ImportError:  # pragma: no cover - deps.py guards this
        return RemoteSizes(sizes={}, known=False)
    sizes: dict[str, int] = {}
    try:
        api = HfApi()
        for start in range(0, len(paths), GET_PATHS_INFO_BATCH):
            batch = paths[start : start + GET_PATHS_INFO_BATCH]
            infos = api.get_paths_info(repo_id, batch, repo_type="dataset", revision=revision)
            for info in infos:
                if info.size is not None:
                    sizes[info.path] = int(info.size)
    except Exception:  # noqa: BLE001 - any hub/network failure falls back to estimate
        return RemoteSizes(sizes=dict(sizes), known=False)
    return RemoteSizes(sizes=sizes, known=True)


@dataclass
class SpaceEstimateParts:
    extracted_bytes: int
    member_count: int
    tar_paths: list[str]
    estimated: bool = False


def estimate_parts(
    *,
    extracted_bytes: int,
    all_rows: int,
    selected_tar_paths: list[str],
) -> SpaceEstimateParts:
    return SpaceEstimateParts(
        extracted_bytes=extracted_bytes,
        member_count=all_rows,
        tar_paths=selected_tar_paths,
    )


def needed_bytes(parts: SpaceEstimateParts, remote: RemoteSizes, meta_tar_size: int | None) -> int:
    """``needed = extracted + cache``; cache = tar blobs (+ meta tar) or fallback."""
    cache = sum(remote.sizes.get(path, 0) for path in parts.tar_paths)
    if not remote.known and parts.tar_paths:
        # Fallback: extracted members + 1 KiB/member each.
        cache = parts.extracted_bytes + FALLBACK_MEMBER_OVERHEAD * parts.member_count
    meta = meta_tar_size if meta_tar_size is not None else (
        FALLBACK_META_TAR_BYTES if not remote.known else 0
    )
    return parts.extracted_bytes + cache + meta


def check_disk(needed: int, data_root: Path, *, force: bool = False) -> str | None:
    """Disk guard. Raises SpaceGuardError unless there is room (or --force).

    Returns a warning string when usage passes the warn threshold, else None.
    """
    free = free_bytes(data_root)
    if needed + GUARD_MARGIN_BYTES > free:
        message = (
            f"not enough free space at {data_root}: "
            f"≈{human_bytes(needed)} needed (+{human_bytes(GUARD_MARGIN_BYTES)} margin), "
            f"{human_bytes(free)} free"
        )
        if not force:
            raise SpaceGuardError(message + "; rerun with --force to override")
        return None
    if free > 0 and needed > WARN_FRACTION * free:
        return (
            f"estimate {human_bytes(needed)} is over {int(WARN_FRACTION * 100)}% of "
            f"free space ({human_bytes(free)})"
        )
    return None


def cache_usage(cache_dir: Path) -> tuple[int, bool]:
    """Bytes of HF cache blobs (including .incomplete) under ``cache_dir``.

    Returns ``(bytes, cache_dir_exists)``. The cache is the authoritative blob
    store — deleting it forfeits resume/reuse.
    """
    cache_dir = Path(cache_dir)
    if not cache_dir.is_dir():
        return 0, False
    return sum(_file_size(p) for p in cache_dir.rglob("*")), True


def _file_size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
    except OSError:  # pragma: no cover - races while walking
        return 0
    return 0