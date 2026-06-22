# MCP Server

A modular Python MCP (Model Context Protocol) server that exposes several categories of tools to AI clients:

- **Filesystem** — read, write, list, search, and inspect files
- **Database** — query and modify SQLite databases (by name alias, not raw path)
- **API** — call external HTTP/REST APIs (by service name alias, not raw URL/key)
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

### API (external HTTP)

Services are configured under `[api.services]` in `config.toml`. Tools use a
`service` name alias; the `base_url` and `api_key` are injected server-side and
never exposed to the model. Call `api_list_services()` first to see what's available.

| Tool | Description |
|------|-------------|
| `api_list_services()` | List configured API service names |
| `api_request(service, method, path, query, json_body)` | Make an HTTP request to a named service; returns `{status, body}` |
| `push_notify(service, title, content, push_to_list)` | Send a Push+ notification; fills the template's `$_title` / `$_content` (content may be inline HTML) |

The `token`/`api_key` is read from the service's `config.toml` block and never exposed to the model. For an internal service whose TLS certificate is not publicly trusted, set `verify = false` in its service block to skip certificate verification.

### Custom / Utility

| Tool | Description |
|------|-------------|
| `echo(message)` | Returns the message unchanged (connectivity test) |
| `system_info()` | Returns Python version, platform, timestamp |
| `calculate(expression)` | Safe math expression evaluator |
| `format_data(data, input_format, output_format)` | JSON ↔ plain-text conversion |

## Deployment

### Docker (recommended for server environments)

```bash
# 1. Copy and edit the config
cp config.toml.example config.toml
# edit config.toml — set allowed_paths and database connections

# 2. Build and start
docker compose up -d

# 3. Check status / logs
docker compose ps
docker compose logs -f
```

The container mounts `./config.toml` as read-only at `/config/config.toml` and
persists data in a named volume `mcp-data`. To write logs to a file, set
`MCP_LOG_FILE=/data/mcp_server.log` in `docker-compose.yml`.

### systemd (Linux bare-metal / VM)

```bash
# Run once as root — creates service user, installs to /opt/mcp-server,
# copies config template to /etc/mcp/config.toml, and enables the service.
sudo bash deploy/install-systemd.sh

# Edit the config before starting
sudo nano /etc/mcp/config.toml

sudo systemctl start mcp-server
sudo systemctl status mcp-server
journalctl -u mcp-server -f      # live logs
```

### Logging

Logging is configured with environment variables (not config.toml):

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `MCP_LOG_FILE`  | _(none)_ | If set, logs are also written to this file |

Logs always go to **stderr** to keep stdout clean for the stdio transport.
Write operations (`write_file`, `delete_file`, `db_execute`, `db_execute_script`)
are logged at INFO level for auditing.

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

## Adding an API

External REST APIs are config-driven — no code needed for the common case.

**1. Add a service to `config.toml`** (API Key / Bearer token example):

```toml
[api.services.weather]
base_url    = "https://api.openweathermap.org/data/2.5"
api_key     = "your-key-here"
auth_header = "Authorization"   # or "X-API-Key"
auth_prefix = "Bearer "         # use "" for X-API-Key style
```

For a public, key-less API just set `base_url`. The model only ever sees the
service name (`weather`) — the key stays on the server, like database aliases.

**2. Call it** via the generic tool:

```
api_request(service="weather", path="/weather", query={"q": "Taipei", "units": "metric"})
```

**3. (Optional) Add a typed convenience wrapper** in `src/mcp_server/tools/api.py`
when you want a clearer, self-documenting tool (e.g. `get_weather(city)`).

### What to give me to wire up a new API

When you want help adding one, the following is enough (most copies straight
from the API's docs):

1. **Service name** — the alias you want (e.g. `weather`, `twse`).
2. **base_url** — the API's root URL.
3. **Auth** — header name (`Authorization` vs `X-API-Key`), prefix (`Bearer ` or
   empty), and the key (a placeholder is fine; put the real key in `config.toml` yourself).
4. **Endpoint(s)** — method + path (e.g. `GET /weather`) and the query/body params.
5. **(Optional) A sample response** — a JSON snippet, so I can build a typed
   wrapper that surfaces just the fields you care about.

Items 1–3 are enough to call the API via `api_request`. Add 4–5 and I can write
a dedicated convenience tool with a clear docstring.

## Project Structure

```
MCP_server/
├── config.toml             # Your local config (gitignored)
├── config.toml.example     # Template — copy and edit
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── deploy/
│   ├── mcp-server.service      # systemd service unit
│   └── install-systemd.sh      # one-shot Linux install script
├── src/
│   └── mcp_server/
│       ├── config.py           # Access control, config loader & startup validation
│       ├── server.py           # Entry point + CLI args
│       ├── tools/
│       │   ├── filesystem.py
│       │   ├── database.py
│       │   ├── api.py
│       │   └── custom.py
│       └── utils/
│           ├── errors.py
│           └── logging.py      # Structured logging setup
└── README.md
```
