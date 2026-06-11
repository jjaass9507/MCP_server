"""Configuration loader for the MCP server.

Reads config.toml (or the path in MCP_CONFIG env var) and exposes two
access-control helpers used by the filesystem and database tools.
"""

import os
import pathlib
import sys
import tomllib
from typing import Any

from mcp_server.utils.errors import ToolError

# Resolve config file path: env var → project root → empty config
def _find_config_path() -> pathlib.Path | None:
    env = os.environ.get("MCP_CONFIG")
    if env:
        return pathlib.Path(env)
    # When installed as a wheel __file__ is inside site-packages, so search
    # from the working directory instead.
    candidate = pathlib.Path.cwd() / "config.toml"
    if candidate.exists():
        return candidate
    return None


def _load() -> dict[str, Any]:
    path = _find_config_path()
    if path is None:
        print(
            "[mcp-server] WARNING: No config.toml found and MCP_CONFIG is not set. "
            "All filesystem and database access will be denied. "
            "Copy config.toml.example to config.toml and edit it.",
            file=sys.stderr,
        )
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


_config: dict[str, Any] = _load()

# ── Filesystem helpers ─────────────────────────────────────────────────────

_allowed_paths: list[pathlib.Path] = [
    pathlib.Path(p).resolve()
    for p in _config.get("filesystem", {}).get("allowed_paths", [])
]
_allow_write: bool = _config.get("filesystem", {}).get("allow_write", False)


def check_path(p: pathlib.Path, write: bool = False) -> None:
    """Raise ToolError if p is outside every allowed directory, or if write
    is requested but allow_write is False.

    Must be called with the already-resolved path.
    """
    if not _allowed_paths:
        raise ToolError(
            "Filesystem access is not configured. "
            "Add allowed_paths under [filesystem] in config.toml."
        )
    allowed = any(
        p == base or base in p.parents
        for base in _allowed_paths
    )
    if not allowed:
        raise ToolError(
            f"Access denied: '{p}' is outside the allowed directories. "
            f"Allowed: {[str(b) for b in _allowed_paths]}"
        )
    if write and not _allow_write:
        raise ToolError(
            "Write access is disabled. Set allow_write = true under [filesystem] in config.toml."
        )


# ── Database helpers ───────────────────────────────────────────────────────

_db_connections: dict[str, str] = (
    _config.get("database", {}).get("connections", {})
)


def resolve_db(name: str) -> str:
    """Return the connection string (SQLite path or PostgreSQL DSN) for a named database."""
    if name not in _db_connections:
        available = list(_db_connections.keys())
        if available:
            raise ToolError(
                f"Unknown database '{name}'. "
                f"Available databases: {available}"
            )
        raise ToolError(
            "No databases are configured. "
            "Add entries under [database.connections] in config.toml."
        )
    return _db_connections[name]


def is_postgres(dsn: str) -> bool:
    """Return True if the connection string is a PostgreSQL DSN."""
    return dsn.startswith(("postgresql://", "postgres://"))


def list_db_names() -> list[str]:
    """Return all configured database names."""
    return list(_db_connections.keys())


# ── Startup validation ─────────────────────────────────────────────────────

class ConfigError(Exception):
    """Raised at startup when the loaded configuration is invalid."""


def validate_config() -> list[str]:
    """Validate the loaded configuration and return a list of warnings.

    Raises ConfigError for problems that should prevent the server from
    starting (e.g. an allowed_path that does not exist). Returns a list of
    non-fatal warnings (e.g. no tools configured at all) that the caller
    should log. This is meant to be called once during server startup so
    misconfiguration surfaces immediately instead of on the first tool call.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if _config == {}:
        warnings.append(
            "No config.toml was loaded — all filesystem and database access is denied."
        )

    # Filesystem: every configured allowed_path must exist and be a directory.
    for p in _allowed_paths:
        if not p.exists():
            errors.append(f"filesystem.allowed_paths entry does not exist: {p}")
        elif not p.is_dir():
            errors.append(f"filesystem.allowed_paths entry is not a directory: {p}")

    if not _allowed_paths and not _db_connections:
        warnings.append(
            "No filesystem paths and no databases are configured — "
            "only the custom utility tools will be usable."
        )

    # Database: validate the shape of each connection string. SQLite paths must
    # have an existing parent directory; PostgreSQL DSNs are well-formed enough.
    for name, dsn in _db_connections.items():
        if not isinstance(dsn, str) or not dsn.strip():
            errors.append(f"database.connections['{name}'] is empty or not a string")
            continue
        if is_postgres(dsn):
            continue  # DSN reachability is checked lazily on first use.
        db_path = pathlib.Path(dsn)
        if not db_path.parent.exists():
            errors.append(
                f"database.connections['{name}'] points to '{dsn}' "
                f"but the parent directory '{db_path.parent}' does not exist"
            )

    if errors:
        raise ConfigError(
            "Invalid configuration:\n  - " + "\n  - ".join(errors)
        )

    return warnings
