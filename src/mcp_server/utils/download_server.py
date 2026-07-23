"""Read-only HTTP download server for cross-machine file handoff.

Tools that write a file to this machine's disk — db_query_to_file /
gms_history_values(to_file=true) (CSV) and create_presentation (.pptx) —
produce a local path that is useless when the client (or another machine's
MCP server) has no access to this filesystem. This module runs a minimal,
read-only HTTP server that streams a registered file back by an unguessable,
time-limited id. The file contents never pass back through this server's own
tool responses; only the download_url does.
"""

import http.server
import pathlib
import secrets
import shutil
import threading
import time
import urllib.parse

import mcp_server.config as cfg
from mcp_server.utils.logging import get_logger

logger = get_logger("download")

_lock = threading.Lock()
_registry: dict[str, tuple[pathlib.Path, float]] = {}

_httpd: http.server.ThreadingHTTPServer | None = None

# Content types for the file kinds this server hands off. Anything else is
# streamed as a generic binary download (the filename in Content-Disposition
# still carries the real extension, so clients save it correctly).
_CONTENT_TYPES: dict[str, str] = {
    ".csv": "text/csv; charset=utf-8",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _purge_expired() -> None:
    """Drop expired registry entries. Caller must hold _lock."""
    now = time.time()
    for file_id in [i for i, (_, expires_at) in _registry.items() if expires_at <= now]:
        del _registry[file_id]


def register_file(path: pathlib.Path) -> str:
    """Register path for download and return its time-limited download URL.

    The id is unguessable (secrets.token_urlsafe) and expires after
    [export] url_ttl_minutes. Expired entries are purged opportunistically
    on every call.
    """
    settings = cfg.get_download_config()
    file_id = secrets.token_urlsafe(16)
    expires_at = time.time() + settings["url_ttl_minutes"] * 60
    with _lock:
        _purge_expired()
        _registry[file_id] = (path, expires_at)
    return f"http://{settings['advertise_host']}:{settings['download_port']}/exports/{file_id}"


class _DownloadHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        parts = parsed.path.split("/")
        # Only "/exports/{id}" is served; everything else 404s, no listing.
        if len(parts) != 3 or parts[1] != "exports" or not parts[2]:
            self.send_error(404)
            return
        file_id = parts[2]

        with _lock:
            _purge_expired()
            entry = _registry.get(file_id)
            if entry is None:
                self.send_error(404)
                return
            path, _expires_at = entry
            if not path.exists():
                # The file may have been removed by the 7-day export cleanup.
                del _registry[file_id]
                self.send_error(404)
                return

        try:
            size = path.stat().st_size
            content_type = _CONTENT_TYPES.get(
                path.suffix.lower(), "application/octet-stream"
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            with path.open("rb") as f:
                shutil.copyfileobj(f, self.wfile)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected mid-stream; nothing to report

    def log_message(self, format: str, *args) -> None:
        # Route logging through the project logger instead of stderr, and
        # never log the full request path — it contains the unguessable
        # download id, so logging it would leak the same capability the id
        # is meant to protect. Only the id's first 8 chars and the status
        # code are logged.
        file_id = self.path.split("?", 1)[0].rsplit("/", 1)[-1]
        status = args[1] if len(args) > 1 else "?"
        logger.info("download request: id=%s... status=%s", file_id[:8], status)


def start_download_server() -> None:
    """Start the read-only download HTTP server as a daemon thread.

    Idempotent: subsequent calls are a no-op once the server is running.
    Reads download_host/download_port from config.get_download_config().
    Raises RuntimeError with a clear message if the port is already in use.
    """
    global _httpd
    if _httpd is not None:
        return
    settings = cfg.get_download_config()
    host = settings["download_host"]
    port = settings["download_port"]
    try:
        httpd = http.server.ThreadingHTTPServer((host, port), _DownloadHandler)
    except OSError as e:
        raise RuntimeError(
            f"Could not start download server on {host}:{port} ({e}). "
            "Is another process already using that port? "
            "Change [export] download_port in config.toml if so."
        ) from e
    _httpd = httpd
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
