"""Helpers shared by the thin command modules (pure formatting/IO)."""

from __future__ import annotations

import json
import sys
from typing import Any

from ..core.model import _to_jsonable


def emit_json(value: Any) -> None:
    print(json.dumps(_to_jsonable(value), indent=2, default=str))


def emit_plain(lines: list[str]) -> None:
    for line in lines:
        print(line)


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def human_bytes(value: float | int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if size < 1024.0 or unit == "PiB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    raise AssertionError("unreachable")