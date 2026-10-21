"""Thin command modules: parse nothing, compute nothing — delegate to api.

Each module exposes ``register(subparsers)`` and ``run(args, client)``.
Business logic lives in core/sources; these only format output.
"""

from . import (  # noqa: F401 - imported so cli.py can dispatch by name
    cache_info,
    doctor,
    download,
    fetch_tool,
    import_data,
    init,
    inspect,
    install_metadata,
    list_categories,
    sources,
    status,
)