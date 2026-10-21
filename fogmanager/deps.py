"""Dependency probes and the exact pip install command.

Our code never imports rich or zstandard — the *official tool* needs them at
runtime — so we install all three as insurance and probe them in ``doctor``.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

from .errors import DependencyError

#: (requirement, import name to probe, distribution name)
DEPS = (
    ("huggingface_hub[cli]>=0.36.0", "huggingface_hub", "huggingface_hub"),
    ("rich", "rich", "rich"),
    ("zstandard", "zstandard", "zstandard"),
    ("platformdirs", "platformdirs", "platformdirs"),
)

REQUIRED_PIP = '"huggingface_hub[cli]>=0.36.0" rich zstandard'


@dataclass
class DepStatus:
    requirement: str
    import_name: str
    ok: bool
    version: str | None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "requirement": self.requirement,
            "ok": self.ok,
            "version": self.version,
            "error": self.error,
        }


def probe() -> list[DepStatus]:
    out: list[DepStatus] = []
    for requirement, import_name, dist_name in DEPS:
        try:
            __import__(import_name)
        except Exception as exc:  # noqa: BLE001 - any import failure is a probe result
            out.append(DepStatus(requirement, import_name, False, None, str(exc)))
            continue
        try:
            out.append(DepStatus(requirement, import_name, True, version(dist_name)))
        except PackageNotFoundError:
            out.append(DepStatus(requirement, import_name, True, None))
    return out


def missing() -> list[DepStatus]:
    return [d for d in probe() if not d.ok]


def pip_command() -> str:
    return f"pip install {REQUIRED_PIP}"


def fix(*, dry_run: bool = False) -> tuple[int, list[str]]:
    """Install all requirements via pip. Returns (exit_code, printed commands)."""
    argv = [
        sys.executable, "-m", "pip", "install", "--upgrade",
        "huggingface_hub[cli]>=0.36.0", "rich", "zstandard", "platformdirs",
    ]
    if dry_run:
        return 0, [" ".join(argv)]
    code = subprocess.run(argv, check=False).returncode
    return code, [" ".join(argv)]


def ensure_core_deps() -> None:
    """Raise DependencyError when a hard-required package is missing."""
    problems = [d for d in missing() if d.import_name in ("huggingface_hub", "platformdirs")]
    if problems:
        detail = "; ".join(f"{d.import_name}: {d.error}" for d in problems)
        raise DependencyError(f"{detail}. Install with: {pip_command()}")


__all__ = [
    "DEPS",
    "REQUIRED_PIP",
    "DepStatus",
    "probe",
    "missing",
    "pip_command",
    "fix",
    "ensure_core_deps",
]