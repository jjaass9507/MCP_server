import argparse

from mcp.server.fastmcp import FastMCP

import mcp_server.config as cfg
from mcp_server.tools import custom, database, filesystem, presentation


def create_server() -> FastMCP:
    mcp = FastMCP(
        name="MCP Server",
        host="0.0.0.0",
        instructions=(
            "A modular MCP server providing filesystem, database, custom, and presentation tools. "
            "Filesystem and database access is restricted to paths configured in config.toml. "
            "Call db_list_databases() to see available databases before querying. "
            "Call list_presentation_styles() to see slide presets before calling create_presentation()."
        ),
    )
    filesystem.register(mcp, cfg)
    database.register(mcp, cfg)
    custom.register(mcp)
    presentation.register(mcp, cfg)
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

    if args.transport == "sse":
        app.settings.host = args.host
        app.settings.port = args.port
        app.run(transport="sse")
    else:
        app.run(transport="stdio")


if __name__ == "__main__":
    main()
