# MCP Server

A modular Python MCP (Model Context Protocol) server that exposes three categories of tools to AI clients such as Claude Desktop:

- **Filesystem** — read, write, list, search, and inspect files
- **Database** — query and modify SQLite databases
- **Custom** — utility tools and a template for adding your own business logic

## Installation

```bash
pip install -e .
```

Requires Python 3.11+.

## Running the Server

```bash
# Via the installed script
mcp-server

# Or directly
python -m mcp_server.server
```

The server communicates over **stdio** (standard MCP transport).

## Testing with MCP Inspector

```bash
npx @modelcontextprotocol/inspector python -m mcp_server.server
```

Opens a browser UI at `http://localhost:5173` where you can call each tool interactively.

## Integrating with Claude Desktop

Add the following to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "my-server": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/MCP_server"
    }
  }
}
```

Restart Claude Desktop after saving the config.

## Available Tools

### Filesystem

| Tool | Description |
|------|-------------|
| `read_file(path)` | Read text file contents (truncates at 1 MB) |
| `write_file(path, content, mode)` | Write or append to a file |
| `list_directory(path, recursive)` | List directory entries with metadata |
| `search_files(directory, pattern, recursive)` | Glob-search for files |
| `file_info(path)` | Get file/directory metadata |
| `delete_file(path)` | Delete a file |

### Database (SQLite)

| Tool | Description |
|------|-------------|
| `db_query(db_path, sql, params)` | Run a SELECT query, returns rows as dicts |
| `db_execute(db_path, sql, params)` | Run INSERT / UPDATE / DELETE |
| `db_list_tables(db_path)` | List all tables in a database |
| `db_table_schema(db_path, table_name)` | Get column definitions for a table |
| `db_execute_script(db_path, script)` | Run a multi-statement SQL script |

### Custom / Utility

| Tool | Description |
|------|-------------|
| `echo(message)` | Returns the message unchanged (connectivity test) |
| `system_info()` | Returns Python version, platform, timestamp |
| `calculate(expression)` | Safe math expression evaluator |
| `format_data(data, input_format, output_format)` | JSON ↔ plain-text conversion |

## Adding New Tools

**Add a tool to an existing category** — open the relevant file in `src/mcp_server/tools/` and add a new decorated function inside `register()`:

```python
@mcp.tool()
def my_new_tool(param: str) -> str:
    """Describe what this tool does."""
    return ...
```

**Add a new tool category** — create a new module and register it:

```python
# src/mcp_server/tools/my_category.py
from mcp.server.fastmcp import FastMCP
from mcp_server.utils.errors import ToolError

def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def my_tool(param: str) -> str:
        """Tool description."""
        ...
```

Then add two lines to `src/mcp_server/server.py`:

```python
from mcp_server.tools import my_category   # import
my_category.register(mcp)                  # register
```

## Project Structure

```
MCP_server/
├── pyproject.toml
├── src/
│   └── mcp_server/
│       ├── server.py           # Entry point
│       ├── tools/
│       │   ├── filesystem.py
│       │   ├── database.py
│       │   └── custom.py       # Demonstration tools + extension template
│       └── utils/
│           └── errors.py       # ToolError base class
└── README.md
```
