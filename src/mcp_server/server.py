from mcp.server.fastmcp import FastMCP

from mcp_server.tools import filesystem, database, custom


def create_server() -> FastMCP:
    mcp = FastMCP(
        name="MCP Server",
        instructions=(
            "A modular MCP server providing filesystem, database, and custom tools. "
            "Use filesystem tools to read/write local files. "
            "Use database tools to query or modify SQLite databases. "
            "Use custom tools for utility operations and business logic."
        ),
    )
    filesystem.register(mcp)
    database.register(mcp)
    custom.register(mcp)
    return mcp


def main() -> None:
    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
