"""Structured logging setup for the MCP server.

Provides a single setup_logging() entry point that configures the root
logger with a consistent format. Logs go to stderr by default (safe for the
stdio transport, which reserves stdout for the protocol) and optionally to a
file when MCP_LOG_FILE is set.

Configuration via environment variables:
    MCP_LOG_LEVEL   Logging level (DEBUG, INFO, WARNING, ERROR). Default: INFO.
    MCP_LOG_FILE    If set, also write logs to this file path.
"""

import logging
import os
import sys

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging() -> logging.Logger:
    """Configure root logging once and return the mcp_server logger.

    Safe to call multiple times — only the first call configures handlers.
    Logs are written to stderr (never stdout, which the stdio transport uses
    for protocol messages) and optionally to the file in MCP_LOG_FILE.
    """
    global _configured
    logger = logging.getLogger("mcp_server")
    if _configured:
        return logger

    level_name = os.environ.get("MCP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    handlers: list[logging.Handler] = []
    # stderr keeps stdout clean for the stdio transport's JSON-RPC stream.
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    handlers.append(stderr_handler)

    log_file = os.environ.get("MCP_LOG_FILE")
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    root = logging.getLogger()
    root.setLevel(level)
    for handler in handlers:
        root.addHandler(handler)

    _configured = True
    logger.info("Logging initialised (level=%s, file=%s)", level_name, log_file or "stderr only")
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a named child logger under the mcp_server namespace."""
    return logging.getLogger(f"mcp_server.{name}")
