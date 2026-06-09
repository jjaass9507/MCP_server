# MCP Server

A modular Python MCP (Model Context Protocol) server that exposes three categories of tools to AI clients:

- **Filesystem** — read, write, list, search, and inspect files
- **Database** — query and modify SQLite databases (by name alias, not raw path)
- **Custom** — utility tools and a template for adding your own business logic

Access to files and databases is controlled by `config.toml` — the model can only touch what you explicitly allow.

## Installation

```bash
pip install -e .
```

Requires Python 3.11+.

## Configuration

Copy the example config and edit it before starting the server:

```bash
cp config.toml.example config.toml
```

```toml
[filesystem]
# Directories the model is allowed to access (absolute paths).
# Empty list = all access denied.
allowed_paths = [
    "/home/user/data",
    "/tmp/workspace",
]
# Set to false to make the server read-only.
allow_write = true

[database]
# Named aliases → actual file paths.
# The model uses the alias (e.g. "mydb"), never the real path.
[database.connections]
mydb      = "/home/user/data/mydb.sqlite"
analytics = "/home/user/data/analytics.sqlite"
```

You can point to a custom config location with the `MCP_CONFIG` environment variable:

```bash
MCP_CONFIG=/etc/mcp/config.toml python -m mcp_server.server
```

## Running the Server

```bash
# stdio transport (for Claude Desktop, MCP Inspector)
python -m mcp_server.server

# SSE / HTTP transport (for Open WebUI, Ollama, web clients)
python -m mcp_server.server --transport sse --host 0.0.0.0 --port 8080
```

## Client Setup

### Claude Desktop

Add to `claude_desktop_config.json`:

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

Restart Claude Desktop after saving.

### Open WebUI + Ollama (地端模型)

Open WebUI supports MCP via SSE transport:

1. Start the server in SSE mode:
   ```bash
   python -m mcp_server.server --transport sse --port 8080
   ```
2. In Open WebUI → **Settings → Tools** → add a new tool server:
   - URL: `http://localhost:8080/sse`
3. Enable the tool server for your model session.

> **Note:** The MCP server itself is model-agnostic. The same server code works with Claude, Ollama, LM Studio (via mcp-proxy), or any agent framework that supports MCP tool calling. Only the client configuration differs.

### MCP Inspector (testing)

```bash
npx @modelcontextprotocol/inspector python -m mcp_server.server
```

Opens a browser UI at `http://localhost:5173` for interactive tool testing.

## Available Tools

### Filesystem

All filesystem tools check that the path is inside `allowed_paths` from config.toml.

| Tool | Description |
|------|-------------|
| `read_file(path)` | Read text file contents (truncates at 1 MB) |
| `write_file(path, content, mode)` | Write or append — requires `allow_write = true` |
| `list_directory(path, recursive)` | List directory entries with metadata |
| `search_files(directory, pattern, recursive)` | Glob-search for files |
| `file_info(path)` | Get file/directory metadata |
| `delete_file(path)` | Delete a file — requires `allow_write = true` |

### Database (SQLite)

Tools use a `db_name` alias from `config.toml` instead of a raw file path. Call `db_list_databases()` first to see what's available.

| Tool | Description |
|------|-------------|
| `db_list_databases()` | List configured database names |
| `db_query(db_name, sql, params)` | SELECT query, returns rows as dicts |
| `db_execute(db_name, sql, params)` | INSERT / UPDATE / DELETE |
| `db_list_tables(db_name)` | List all tables |
| `db_table_schema(db_name, table_name)` | Get column definitions |
| `db_execute_script(db_name, script)` | Run a multi-statement SQL script |

### Custom / Utility

| Tool | Description |
|------|-------------|
| `echo(message)` | Returns the message unchanged (connectivity test) |
| `system_info()` | Returns Python version, platform, timestamp |
| `calculate(expression)` | Safe math expression evaluator |
| `format_data(data, input_format, output_format)` | JSON ↔ plain-text conversion |

## Adding New Tools

**Add a tool to an existing category** — open the file in `src/mcp_server/tools/` and add inside `register()`:

```python
@mcp.tool()
def my_new_tool(param: str) -> str:
    """Describe what this tool does."""
    return ...
```

**Add a new tool category** — create a new module:

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
from mcp_server.tools import my_category
my_category.register(mcp)
```

## Project Structure

```
MCP_server/
├── config.toml             # Your local config (gitignored)
├── config.toml.example     # Template — copy and edit
├── pyproject.toml
├── src/
│   └── mcp_server/
│       ├── config.py           # Access control & config loader
│       ├── server.py           # Entry point + CLI args
│       ├── tools/
│       │   ├── filesystem.py
│       │   ├── database.py
│       │   └── custom.py
│       └── utils/
│           └── errors.py
└── README.md
```
