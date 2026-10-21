"""Official VfxDB downloader: tool acquisition and the invocation seam.

We orchestrate; we never reimplement the download spec. Every interaction
with the official tool goes through :func:`run_official` — the monkeypatch
point for tests.

Invocation contract:

- ``[sys.executable, "-I", <tool>, <abs data_root>, "--cache-dir", <abs cache_dir>, ...]``
  — absolute paths (the tool's default data root is relative ``data/vdbset``),
  ``-I`` per our untrusted-input posture, list argv, inherited stdio so the
  tool's rich progress streams live (never captured).
- The bootstrap fetches its sibling implementation files from ``main``
  regardless of ``--revision`` — not pin-reproducible; sha256 provenance is
  recorded in the source config entry, and ``--cache-dir`` is ALWAYS passed so
  those code blobs land in our cache, never on the system drive.
- Child exit codes propagate verbatim: 0 ok, 130 Ctrl-C, 1 error, 2 non-TTY TUI.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import platformdirs

from ...errors import ToolError

REPO_ID = "ryogishiki/VfxDB"
REPO_TYPE = "dataset"
DEFAULT_REVISION = "main"
TOOL_FILENAME = "download_extract_data.py"
SIBLING_FILENAMES = ("vfxdb_downloader.py", "vfxdb_tui.py")
VALID_PRESETS = ("smoke", "medium", "full")

#: The 11 upstream categories (table printed when metadata is not installed).
OFFICIAL_CATEGORIES = (
    "BuoyantExplosion",
    "CloudWave",
    "EnvironmentalFog",
    "IsotropicBurst",
    "LiquidSplash",
    "RingBlast",
    "RisingFlame",
    "SmokePlume",
    "SurfaceFire",
    "ViscousFlow",
    "VortexColumn",
)


def display_command(argv: list[str]) -> str:
    """Platform-accurate quoting for display only (never for execution)."""
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


# ---------------------------------------------------------------- tool fetch


def default_tools_dir() -> Path:
    """Where fetched official tool files live.

    Prefer a checkout's ``tools/`` (gitignored contents; matches the repo
    layout), falling back to the platform user-data dir for installed copies.
    """
    repo_root = Path(__file__).resolve().parents[3]
    checkout_tools = repo_root / "tools"
    if checkout_tools.is_dir() and os.access(checkout_tools, os.W_OK):
        return checkout_tools
    return Path(platformdirs.user_data_dir("FogManager", appauthor=False)) / "tools"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class ToolState:
    tool_path: Path
    sha256s: dict[str, str] = field(default_factory=dict)
    fetched_revision: str | None = None
    fetched_at: str | None = None
    changed: bool = False


def fetch_tool(
    *,
    tools_dir: Path,
    cache_dir: Path,
    revision: str | None = None,
    refresh: bool = False,
) -> ToolState:
    """Fetch/refresh the official bootstrap via the HF cache, then copy locally.

    Uses ``hf_hub_download`` with ``cache_dir`` (never the user's default), so
    both the tool blob and the bootstrap-fetched siblings stay in our cache.
    Siblings are hashed best-effort from the cache snapshot.
    """
    from huggingface_hub import hf_hub_download

    tools_dir = Path(tools_dir)
    cache_dir = Path(cache_dir)
    tools_dir.mkdir(parents=True, exist_ok=True)
    target = tools_dir / TOOL_FILENAME

    if target.is_file() and not refresh:
        return ToolState(
            tool_path=target,
            sha256s={TOOL_FILENAME: sha256_file(target)},
            changed=False,
        )

    effective_revision = revision or DEFAULT_REVISION
    try:
        downloaded = hf_hub_download(
            repo_id=REPO_ID,
            repo_type=REPO_TYPE,
            filename=f"tools/{TOOL_FILENAME}",
            revision=effective_revision,
            cache_dir=str(cache_dir),
        )
    except Exception as exc:  # noqa: BLE001 - report, don't traceback, on hub errors
        raise ToolError(f"cannot fetch official bootstrap: {exc}") from exc

    shutil.copyfile(downloaded, target)
    sha256s = {TOOL_FILENAME: sha256_file(target)}

    # Best-effort: hash any siblings already next to the downloaded file in
    # the cache snapshot.
    snapshot = Path(downloaded).parent
    for name in SIBLING_FILENAMES:
        sibling = snapshot / name
        if sibling.is_file():
            sha256s[name] = sha256_file(sibling)

    return ToolState(
        tool_path=target,
        sha256s=sha256s,
        fetched_revision=effective_revision,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        changed=True,
    )


# ------------------------------------------------------------------ argv/run


def build_argv(
    data_root: Path,
    cache_dir: Path,
    *,
    preset: str | None = None,
    percentage: str | None = None,
    categories: tuple[str, ...] | list[str] = (),
    max_samples: int | None = None,
    include_bad: bool = False,
    revision: str | None = None,
    tool_path: Path | None = None,
    executable: str | None = None,
) -> list[str]:
    """Exact argv for the official tool. Always absolute paths + --cache-dir."""
    modes = [preset is not None, percentage is not None, bool(categories or max_samples is not None)]
    if sum(bool(m) for m in modes) > 1:
        raise ToolError("--preset, --percentage, and --category/--max-samples are mutually exclusive")
    if bool(categories) != (max_samples is not None):
        raise ToolError("--category and --max-samples must be provided together")
    if preset is not None and preset not in VALID_PRESETS:
        raise ToolError(f"preset must be one of {VALID_PRESETS}")

    tool = Path(tool_path) if tool_path is not None else default_tools_dir() / TOOL_FILENAME
    if not tool.is_file():
        raise ToolError(
            f"official downloader not found at {tool}; run 'fogmgr fetch-tool' first"
        )
    argv = [
        executable or sys.executable,
        "-I",
        str(tool),
        str(Path(data_root).expanduser().resolve()),
        "--cache-dir",
        str(Path(cache_dir).expanduser().resolve()),
    ]
    if preset is not None:
        argv += ["--preset", preset]
    if percentage is not None:
        argv += ["--percentage", percentage]
    if categories:
        argv += ["--max-samples", str(max_samples)]
        for category in categories:
            argv += ["--category", category]
    if include_bad:
        argv += ["--include-bad"]
    if revision:
        argv += ["--revision", revision]
    return argv


#: Interpretation of official exit codes (documented tool contract).
EXIT_MEANINGS = {
    0: "success",
    130: "interrupted (Ctrl-C); rerun the same command to continue",
    1: "error (the tool printed stage/path/cause above)",
    2: "non-interactive terminal; the tool cannot run its TUI here",
}


def run_official(argv: list[str]) -> int:
    """The single seam through which the official tool ever executes.

    Inherited stdio (never captured) so rich progress streams live; the exact
    command is echoed first; the child's exit code is returned verbatim.
    """
    print(f"+ {display_command(argv)}", flush=True)
    result = subprocess.run(argv, check=False)
    return int(result.returncode)