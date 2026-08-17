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

# ── Tool registration profiles ────────────────────────────────────────────

TOOL_CATEGORIES = frozenset(
    {"filesystem", "database", "custom", "api", "presentation", "gms"}
)
TOOL_PROFILES: dict[str, frozenset[str]] = {
    "all": TOOL_CATEGORIES,
    "core": frozenset({"filesystem", "database", "custom", "api"}),
    "gms": frozenset({"custom", "gms"}),
    "presentation": frozenset({"custom", "presentation"}),
    "minimal": frozenset({"custom"}),
}

_tools_cfg: Any = _config.get("tools", {})
_tool_profile: Any = (
    _tools_cfg.get("profile", "all") if isinstance(_tools_cfg, dict) else "all"
)
_enabled_categories: Any = (
    _tools_cfg.get("enabled_categories") if isinstance(_tools_cfg, dict) else None
)


def get_enabled_tool_categories() -> frozenset[str]:
    """Return categories that should be registered for this process.

    Invalid values fall back to the backwards-compatible ``all`` profile here;
    validate_config() reports and rejects them before the transport starts.
    An explicit enabled_categories list overrides the selected profile.
    """
    if _enabled_categories is not None:
        if (
            isinstance(_enabled_categories, list)
            and all(isinstance(item, str) for item in _enabled_categories)
            and set(_enabled_categories) <= TOOL_CATEGORIES
        ):
            return frozenset(_enabled_categories)
        return TOOL_PROFILES["all"]
    if isinstance(_tool_profile, str) and _tool_profile in TOOL_PROFILES:
        return TOOL_PROFILES[_tool_profile]
    return TOOL_PROFILES["all"]


# ── HTTP transport ────────────────────────────────────────────────────────

_http_cfg: Any = _config.get("http", {})
_http_host: Any = (
    _http_cfg.get("host", "127.0.0.1")
    if isinstance(_http_cfg, dict)
    else "127.0.0.1"
)
_http_port: Any = (
    _http_cfg.get("port", 8080) if isinstance(_http_cfg, dict) else 8080
)
_http_allowed_hosts: Any = (
    _http_cfg.get(
        "allowed_hosts", ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    )
    if isinstance(_http_cfg, dict)
    else []
)
_http_allowed_origins: Any = (
    _http_cfg.get(
        "allowed_origins",
        ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    )
    if isinstance(_http_cfg, dict)
    else []
)
_http_bearer_token_env: Any = (
    _http_cfg.get("bearer_token_env", "MCP_HTTP_BEARER_TOKEN")
    if isinstance(_http_cfg, dict)
    else "MCP_HTTP_BEARER_TOKEN"
)


def get_http_config() -> dict[str, Any]:
    """Return validated-at-startup HTTP transport settings."""
    token = (
        os.environ.get(_http_bearer_token_env, "")
        if isinstance(_http_bearer_token_env, str) and _http_bearer_token_env
        else ""
    )
    return {
        "host": _http_host,
        "port": _http_port,
        "allowed_hosts": list(_http_allowed_hosts)
        if isinstance(_http_allowed_hosts, list)
        else [],
        "allowed_origins": list(_http_allowed_origins)
        if isinstance(_http_allowed_origins, list)
        else [],
        "bearer_token_env": _http_bearer_token_env,
        "bearer_token": token,
    }


def validate_http_runtime(host: str, port: int, *, transport: str) -> None:
    """Reject unsafe runtime combinations after CLI overrides are known."""
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    http = get_http_config()
    if isinstance(port, bool) or not 1 <= port <= 65535:
        raise ConfigError("HTTP port must be an integer between 1 and 65535.")
    if (
        transport == "streamable-http"
        and http["bearer_token"]
        and len(http["bearer_token"]) < 32
    ):
        raise ConfigError("HTTP bearer token must contain at least 32 characters.")
    if transport == "sse" and not loopback:
        raise ConfigError(
            "Deprecated SSE transport is restricted to localhost. Use streamable-http "
            "for network clients."
        )
    if transport == "streamable-http" and not loopback and not http["bearer_token"]:
        raise ConfigError(
            "Non-local Streamable HTTP requires bearer authentication. Set "
            "[http] bearer_token_env to an environment-variable name and define a "
            "strong token in that environment variable."
        )

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
_db_pool_size: int = _config.get("database", {}).get("pool_size", 5)
_db_access: dict[str, Any] = _config.get("database", {}).get("access", {})
_oracle_call_timeout_seconds: Any = _config.get("database", {}).get(
    "oracle_call_timeout_seconds", 60
)


def get_db_pool_size() -> int:
    """Return the max concurrent connections kept per PostgreSQL/SQL Server/Oracle DSN.

    SQLite is exempt — it always connects directly to a local file.
    """
    return _db_pool_size


def get_oracle_call_timeout_ms() -> int | None:
    """Return the Oracle round-trip call timeout in milliseconds, or None to disable.

    Oracle connections otherwise have no statement timeout: a query that scans
    far more rows than intended (e.g. a wide tag/date range) blocks until the
    server responds or the MCP client's own request timeout gives up. Setting
    oracledb.Connection.call_timeout caps each individual round trip instead.
    """
    if not _oracle_call_timeout_seconds:
        return None
    return int(_oracle_call_timeout_seconds * 1000)


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


def check_db_write(name: str, *, script: bool = False) -> None:
    """Require explicit per-alias permission for database writes."""
    policy = _db_access.get(name, {})
    if not isinstance(policy, dict) or policy.get("read_only", True):
        raise ToolError(
            f"Database '{name}' is read-only. Set read_only = false under "
            f"[database.access.{name}] to enable INSERT/UPDATE/DELETE."
        )
    if script and not policy.get("allow_scripts", False):
        raise ToolError(
            f"SQL scripts are disabled for database '{name}'. Set allow_scripts = true "
            f"under [database.access.{name}] only if multi-statement execution is required."
        )


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

    # Tool registration: profile supplies a preset; enabled_categories, when
    # present, is an exact override. Validate both even when the override is
    # used so stale or misspelled configuration never goes unnoticed.
    if not isinstance(_tools_cfg, dict):
        errors.append("tools must be a table.")
    else:
        if not isinstance(_tool_profile, str) or _tool_profile not in TOOL_PROFILES:
            errors.append(
                "tools.profile must be one of: " + ", ".join(sorted(TOOL_PROFILES)) + "."
            )
        if _enabled_categories is not None:
            if not isinstance(_enabled_categories, list) or not all(
                isinstance(item, str) for item in _enabled_categories
            ):
                errors.append("tools.enabled_categories must be an array of strings.")
            else:
                unknown = sorted(set(_enabled_categories) - TOOL_CATEGORIES)
                if unknown:
                    errors.append(
                        "tools.enabled_categories contains unknown categories: "
                        + ", ".join(unknown)
                        + "."
                    )
                if len(_enabled_categories) != len(set(_enabled_categories)):
                    errors.append("tools.enabled_categories must not contain duplicates.")
                if not _enabled_categories:
                    warnings.append(
                        "tools.enabled_categories is empty — the server will expose no tools."
                    )

    # Streamable HTTP defaults to localhost and validates Host/Origin headers.
    if not isinstance(_http_cfg, dict):
        errors.append("http must be a table.")
    else:
        if not isinstance(_http_host, str) or not _http_host.strip():
            errors.append("http.host must be a non-empty string.")
        if (
            not isinstance(_http_port, int)
            or isinstance(_http_port, bool)
            or not 1 <= _http_port <= 65535
        ):
            errors.append("http.port must be an integer between 1 and 65535.")
        for name, value in (
            ("allowed_hosts", _http_allowed_hosts),
            ("allowed_origins", _http_allowed_origins),
        ):
            if not isinstance(value, list) or not value or not all(
                isinstance(item, str) and item.strip() for item in value
            ):
                errors.append(f"http.{name} must be a non-empty array of strings.")
        if not isinstance(_http_bearer_token_env, str):
            errors.append("http.bearer_token_env must be a string.")

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
            "No filesystem paths and no databases are configured — tools that "
            "depend on those resources will not be usable."
        )

    # Database: validate the shape of each connection string. SQLite paths must
    # have an existing parent directory; PostgreSQL DSNs are well-formed enough.
    if not isinstance(_db_pool_size, int) or _db_pool_size < 1:
        errors.append(
            f"database.pool_size must be a positive integer, got {_db_pool_size!r}."
        )
    _oracle_timeout_valid = _oracle_call_timeout_seconds is None or (
        _oracle_call_timeout_seconds is False
        or (
            isinstance(_oracle_call_timeout_seconds, (int, float))
            and not isinstance(_oracle_call_timeout_seconds, bool)
            and _oracle_call_timeout_seconds >= 0
        )
    )
    if not _oracle_timeout_valid:
        errors.append(
            "database.oracle_call_timeout_seconds must be a non-negative number "
            f"or 0/false to disable, got {_oracle_call_timeout_seconds!r}."
        )
    if not isinstance(_db_access, dict):
        errors.append("database.access must be a table keyed by database alias.")
    else:
        for name, policy in _db_access.items():
            if name not in _db_connections:
                errors.append(f"database.access contains unknown database alias '{name}'.")
                continue
            if not isinstance(policy, dict):
                errors.append(f"database.access.{name} must be a table.")
                continue
            for option in ("read_only", "allow_scripts"):
                if option in policy and not isinstance(policy[option], bool):
                    errors.append(
                        f"database.access.{name}.{option} must be true or false."
                    )
            if policy.get("read_only", True) and policy.get("allow_scripts", False):
                errors.append(
                    f"database.access.{name}.allow_scripts cannot be true when read_only is true."
                )
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
        methods = svc.get("allowed_methods", ["GET"])
        supported_methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
        if not isinstance(methods, list) or not methods or not all(
            isinstance(method, str) and method.upper() in supported_methods
            for method in methods
        ):
            errors.append(
                f"api.services['{name}'].allowed_methods must be a non-empty array "
                "containing only GET, POST, PUT, PATCH, or DELETE"
            )
        prefixes = svc.get("allowed_path_prefixes", ["/"])
        if not isinstance(prefixes, list) or not prefixes or not all(
            isinstance(prefix, str) and prefix.startswith("/") for prefix in prefixes
        ):
            errors.append(
                f"api.services['{name}'].allowed_path_prefixes must be a non-empty "
                "array of paths beginning with '/'"
            )
        timeout = svc.get("timeout_seconds", 30)
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not 0 < timeout <= 120
        ):
            errors.append(
                f"api.services['{name}'].timeout_seconds must be greater than 0 "
                "and no more than 120"
            )
        max_body = svc.get("max_request_body_bytes", 100_000)
        if (
            not isinstance(max_body, int)
            or isinstance(max_body, bool)
            or not 1 <= max_body <= 1_000_000
        ):
            errors.append(
                f"api.services['{name}'].max_request_body_bytes must be an integer "
                "between 1 and 1000000"
            )

    if errors:
        raise ConfigError(
            "Invalid configuration:\n  - " + "\n  - ".join(errors)
        )

    return warnings
