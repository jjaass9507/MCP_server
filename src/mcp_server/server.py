import argparse
import sys

from mcp.server.fastmcp import FastMCP

import mcp_server.config as cfg
from mcp_server.tools import api, custom, database, filesystem, gms, presentation
from mcp_server.utils.logging import setup_logging

logger = setup_logging()


def create_server() -> FastMCP:
    mcp = FastMCP(
        name="MCP Server",
        host="0.0.0.0",
        instructions=(
            "A modular MCP server providing filesystem, database, custom, API, presentation, and "
            "GMS compressed-air query tools. "
            "Filesystem and database access is restricted to paths configured in config.toml. "
            "For compressed-air equipment/point/tag/value queries, prefer the gms_* tools "
            "(gms_list_equipment, gms_list_points, gms_list_pipe_points, gms_realtime_values, "
            "gms_history_values) over hand-written SQL — they encode the fixed PostgreSQL/Oracle "
            "join, zone, and batching logic. Fall back to db_query for ad-hoc or exploratory queries. "
            "Call db_list_databases() to see available databases before querying. "
            "Call api_list_services() to see available external APIs before calling api_request(). "
            "Use push_notify() to send a Push+ notification to email or a group; always format its "
            "content as clean inline HTML and verify the sent_content in the result is correct. "
            "Call list_presentation_styles() to see slide presets before calling create_presentation()."
        ),
    )
    filesystem.register(mcp, cfg)
    database.register(mcp, cfg)
    custom.register(mcp)
    api.register(mcp, cfg)
    presentation.register(mcp, cfg)
    gms.register(mcp, cfg)
    return mcp


app = create_server()


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport protocol (default: stdio). Use 'sse' for HTTP clients like Open WebUI.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host for SSE transport (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Port for SSE transport (default: 8080)")
    args = parser.parse_args()

    # Surface configuration problems at startup instead of on the first tool call.
    try:
        for warning in cfg.validate_config():
            logger.warning("%s", warning)
    except cfg.ConfigError as e:
        logger.error("Refusing to start: %s", e)
        sys.exit(1)

    logger.info("Starting MCP Server (transport=%s)", args.transport)

    if args.transport == "sse":
        app.settings.host = args.host
        app.settings.port = args.port
        app.run(transport="sse")
    else:
        app.run(transport="stdio")


if __name__ == "__main__":
    main()
