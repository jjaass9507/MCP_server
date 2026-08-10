import argparse
import sys

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

import mcp_server.config as cfg
from mcp_server.tools import api, custom, database, filesystem, gms, presentation
from mcp_server.utils.download_server import start_download_server
from mcp_server.utils.http_auth import BearerTokenMiddleware
from mcp_server.utils.logging import setup_logging
from mcp_server.utils.version import code_info

logger = setup_logging()


def _server_instructions(categories: frozenset[str]) -> str:
    enabled = ", ".join(sorted(categories)) or "none"
    parts = [
        f"A modular MCP server. Enabled tool categories: {enabled}. ",
    ]
    if "filesystem" in categories:
        parts.append(
            "Filesystem access is restricted to paths configured in config.toml. "
            "Call fs_list_allowed_paths() before using filesystem tools. "
        )
    if "database" in categories:
        parts.append(
            "Database access uses aliases configured in config.toml. "
            "Call db_list_databases() before querying. For large result sets, use "
            "db_query_to_file instead of embedding or paging through the complete result. "
        )
    if "api" in categories:
        parts.append(
            "Call api_list_services() before api_request(). Use push_notify() for Push+ "
            "notifications; format content as clean inline HTML and verify sent_content. "
        )
    if "presentation" in categories:
        parts.append(
            "Call list_presentation_styles() before create_presentation(). "
        )
    if "gms" in categories:
        fallback = (
            " Fall back to db_query for ad-hoc queries."
            if "database" in categories
            else ""
        )
        parts.append(
            "For compressed-air equipment, point, tag, and value queries, use gms_* tools; "
            "they encode the fixed PostgreSQL/Oracle joins, zones, and batching. Prefer "
            "gms_history_aggregate for ranges over one day or trend-only analysis."
            + fallback
            + " Use to_file=true for large history results. "
        )
    if categories & {"database", "gms"}:
        parts.append(
            "Inline list/query tools return bounded pages; when truncated=true, call the "
            "same tool with cursor=next_cursor and unchanged filters. Never read a large "
            "exported CSV back into context. If another machine must process it, pass that "
            "server the download_url when present. "
        )
    return "".join(parts).strip()


def create_server() -> FastMCP:
    categories = cfg.get_enabled_tool_categories()
    http = cfg.get_http_config()
    mcp = FastMCP(
        name="MCP Server",
        host=http["host"],
        port=http["port"],
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=http["allowed_hosts"],
            allowed_origins=http["allowed_origins"],
        ),
        instructions=_server_instructions(categories),
    )
    if "filesystem" in categories:
        filesystem.register(mcp, cfg)
    if "database" in categories:
        database.register(mcp, cfg)
    if "custom" in categories:
        custom.register(mcp)
    if "api" in categories:
        api.register(mcp, cfg)
    if "presentation" in categories:
        presentation.register(mcp, cfg)
    if "gms" in categories:
        gms.register(mcp, cfg)
    return mcp


app = create_server()


def _run_streamable_http(server: FastMCP, bearer_token: str) -> None:
    """Run the SDK's Streamable HTTP ASGI app with optional bearer auth."""
    import uvicorn

    asgi_app = server.streamable_http_app()
    if bearer_token:
        asgi_app = BearerTokenMiddleware(
            asgi_app, bearer_token, server.settings.streamable_http_path
        )
    uvicorn.run(
        asgi_app,
        host=server.settings.host,
        port=server.settings.port,
        log_level=server.settings.log_level.lower(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help=(
            "Transport protocol (default: stdio). Use streamable-http for network "
            "clients; sse is deprecated and local-only."
        ),
    )
    parser.add_argument(
        "--host", default=None, help="HTTP bind host (default: [http] host or 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=None, help="HTTP port (default: [http] port or 8080)"
    )
    args = parser.parse_args()

    # Surface configuration problems at startup instead of on the first tool call.
    try:
        for warning in cfg.validate_config():
            logger.warning("%s", warning)
        http = cfg.get_http_config()
        host = args.host or http["host"]
        port = args.port or http["port"]
        cfg.validate_http_runtime(host, port, transport=args.transport)
    except cfg.ConfigError as e:
        logger.error("Refusing to start: %s", e)
        sys.exit(1)

    download_cfg = cfg.get_download_config()
    if download_cfg["serve_downloads"]:
        start_download_server()
        logger.info(
            "Download server listening on %s:%s",
            download_cfg["download_host"], download_cfg["download_port"],
        )

    info = code_info()
    logger.info(
        "Running code at %s (git %s, branch %s)",
        info["code_path"], info["git_commit"], info["git_branch"],
    )
    logger.info("Starting MCP Server (transport=%s)", args.transport)

    if args.transport in {"sse", "streamable-http"}:
        app.settings.host = host
        app.settings.port = port
    if args.transport == "streamable-http":
        _run_streamable_http(app, http["bearer_token"])
    elif args.transport == "sse":
        logger.warning(
            "SSE transport is deprecated; migrate clients to http://%s:%s/mcp.",
            app.settings.host,
            app.settings.port,
        )
        app.run(transport="sse")
    else:
        app.run(transport="stdio")


if __name__ == "__main__":
    main()
