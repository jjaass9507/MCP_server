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
_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.parent  # repo root

def _find_config_path() -> pathlib.Path | None:
    env = os.environ.get("MCP_CONFIG")
    if env:
        return pathlib.Path(env)
    candidate = _PROJECT_ROOT / "config.toml"
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
    """Return the filesystem path for a named database.

    Raises ToolError with the list of available names if not found.
    """
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


def list_db_names() -> list[str]:
    """Return all configured database names."""
    return list(_db_connections.keys())
