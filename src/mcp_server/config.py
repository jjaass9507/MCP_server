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

# ── Export directory (large query results written to CSV) ──────────────────

_export_dir_str: str = _config.get("export", {}).get("dir", "")
_export_dir: pathlib.Path | None = (
    pathlib.Path(_export_dir_str).resolve() if _export_dir_str else None
)
if _export_dir is not None:
    # Let existing file tools (read_file for a quick preview, push_notify for
    # image_path) reach the export dir too.
    _allowed_paths.append(_export_dir)


def get_export_dir() -> pathlib.Path:
    """Return the configured export directory for large query results.

    Raises ToolError if [export] dir is not set in config.toml.
    """
    if _export_dir is None:
        raise ToolError(
            "Export directory is not configured. "
            'Add [export] dir = "..." (an absolute path) in config.toml.'
        )
    return _export_dir


# ── Download server (cross-machine CSV handoff) ─────────────────────────────

_export_cfg: dict[str, Any] = _config.get("export", {})
_serve_downloads: bool = _export_cfg.get("serve_downloads", False)
_download_host: str = _export_cfg.get("download_host", "0.0.0.0")
_advertise_host: str = _export_cfg.get("advertise_host", "")
_download_port: int = _export_cfg.get("download_port", 8081)
_url_ttl_minutes: int = _export_cfg.get("url_ttl_minutes", 60)


def get_download_config() -> dict[str, Any]:
    """Return the [export] download-server settings.

    Always returns a dict regardless of whether serve_downloads is enabled;
    validate_config() is what enforces the required fields when it is.
    """
    return {
        "serve_downloads": _serve_downloads,
        "download_host": _download_host,
        "advertise_host": _advertise_host,
        "download_port": _download_port,
        "url_ttl_minutes": _url_ttl_minutes,
    }


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


def is_mssql(dsn: str) -> bool:
    """Return True if the connection string is a SQL Server (MSSQL) DSN."""
    return dsn.startswith(("mssql://", "sqlserver://"))


def is_oracle(dsn: str) -> bool:
    """Return True if the connection string is an Oracle DSN."""
    return dsn.startswith("oracle://")


def list_db_names() -> list[str]:
    """Return all configured database names."""
    return list(_db_connections.keys())


# ── API helpers ────────────────────────────────────────────────────────────

_api_services: dict[str, Any] = _config.get("api", {}).get("services", {})


def list_api_names() -> list[str]:
    """Return all configured API service names."""
    return list(_api_services.keys())


def resolve_api(name: str) -> dict:
    """Return the configuration dict for a named API service."""
    if name not in _api_services:
        available = list(_api_services.keys())
        if available:
            raise ToolError(
                f"Unknown API service '{name}'. "
                f"Available services: {available}"
            )
        raise ToolError(
            "No API services are configured. "
            "Add entries under [api.services] in config.toml."
        )
    return _api_services[name]


# ── Presentation defaults ──────────────────────────────────────────────────

_presentation_cfg: dict[str, Any] = _config.get("presentation", {})

presentation_defaults: dict[str, Any] = {
    "preset":      _presentation_cfg.get("default_preset", ""),
    "title_font":  _presentation_cfg.get("default_title_font", ""),
    "body_font":   _presentation_cfg.get("default_body_font", ""),
    "show_footer": _presentation_cfg.get("default_show_footer", None),
}


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
    # (_export_dir is also appended to _allowed_paths for check_path() access,
    # but it's validated separately below with a clearer message, so skip it here.)
    for p in _allowed_paths:
        if p == _export_dir:
            continue
        if not p.exists():
            errors.append(f"filesystem.allowed_paths entry does not exist: {p}")
        elif not p.is_dir():
            errors.append(f"filesystem.allowed_paths entry is not a directory: {p}")

    # Export directory: must exist and be a directory if configured.
    if _export_dir is not None:
        if not _export_dir.exists():
            errors.append(f"export.dir does not exist: {_export_dir}")
        elif not _export_dir.is_dir():
            errors.append(f"export.dir is not a directory: {_export_dir}")

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
        if dsn.startswith("jdbc:"):
            errors.append(
                f"database.connections['{name}'] uses a JDBC URL ('{dsn}'). "
                f"JDBC URLs are not supported. Use a Python DSN instead, e.g. "
                f"'mssql://user:password@host:port/dbname' for SQL Server."
            )
            continue
        if is_postgres(dsn) or is_mssql(dsn) or is_oracle(dsn):
            continue  # DSN reachability is checked lazily on first use.
        db_path = pathlib.Path(dsn)
        if not db_path.parent.exists():
            errors.append(
                f"database.connections['{name}'] points to '{dsn}' "
                f"but the parent directory '{db_path.parent}' does not exist"
            )

    # Download server: only validated when export.serve_downloads is enabled.
    if _serve_downloads:
        if _export_dir is None:
            errors.append(
                "export.serve_downloads is true but export.dir is not set. "
                'Add [export] dir = "..." (an absolute path) in config.toml.'
            )
        if not _advertise_host.strip():
            errors.append(
                "export.serve_downloads is true but export.advertise_host is empty. "
                "This server cannot auto-detect its own externally reachable IP — "
                "set [export] advertise_host to the address other machines should use to reach it."
            )
        if not isinstance(_download_port, int) or not (1 <= _download_port <= 65535):
            errors.append(
                f"export.download_port must be an integer between 1 and 65535, got {_download_port!r}."
            )
        if not isinstance(_url_ttl_minutes, int) or _url_ttl_minutes <= 0:
            errors.append(
                f"export.url_ttl_minutes must be a positive integer, got {_url_ttl_minutes!r}."
            )

    # API: every configured service must have a non-empty base_url string.
    for name, svc in _api_services.items():
        if not isinstance(svc, dict):
            errors.append(f"api.services['{name}'] must be a table (key = value entries)")
            continue
        base_url = svc.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            errors.append(f"api.services['{name}'] is missing a non-empty 'base_url'")

    if errors:
        raise ConfigError(
            "Invalid configuration:\n  - " + "\n  - ".join(errors)
        )

    return warnings
