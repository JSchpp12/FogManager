"""``fogmgr`` — argparse tree over the stable API. No business logic here.

Global flags ``--config PATH`` and ``--json`` are accepted before or after the
subcommand (spec order is ``fogmgr [--config PATH] [--json] <command> ...``).
Exit codes: 0 ok; 1 FogManager error; child exit codes (130 Ctrl-C, 1 tool
error, 2 non-TTY TUI) propagate verbatim from download.
"""

from __future__ import annotations

import argparse
import sys

from . import commands as command_modules
from . import deps
from .api import Client
from .errors import FogManagerError

_COMMANDS = (
    "init",
    "doctor",
    "sources",
    "fetch_tool",
    "install_metadata",
    "download",
    "import_data",
    "status",
    "list_categories",
    "inspect",
    "cache_info",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fogmgr",
        description="Manage volumetric (OpenVDB) datasets; VfxDB via the official downloader.",
    )
    parser.add_argument("--config", metavar="PATH", help="config file (default: platformdirs)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in _COMMANDS:
        module = getattr(command_modules, name)
        module.register(subparsers)
    return parser


def extract_globals(argv: list[str]) -> tuple[str | None, bool, list[str]]:
    """Pull out global flags wherever they appear; return (config, json, rest)."""
    config: str | None = None
    json_mode = False
    rest: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--json":
            json_mode = True
        elif token == "--config":
            index += 1
            if index >= len(argv):
                raise SystemExit("fogmgr: error: --config requires a path")
            config = argv[index]
        elif token.startswith("--config="):
            config = token.split("=", 1)[1]
        else:
            rest.append(token)
        index += 1
    return config, json_mode, rest


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    global_config, global_json, filtered = extract_globals(argv)
    parser = build_parser()
    args = parser.parse_args(filtered)
    if global_config is not None:
        args.config = global_config
    if global_json:
        args.json = True

    deps.ensure_core_deps()
    try:
        client = Client.load(args.config)
        return int(args.func(args, client))
    except FogManagerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))