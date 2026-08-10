# MCP Server

[![CI](https://github.com/jjaass9507/MCP_server/actions/workflows/ci.yml/badge.svg)](https://github.com/jjaass9507/MCP_server/actions/workflows/ci.yml)

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

The core install supports SQLite without a separate driver. Install only the
drivers used by your configured database aliases:

```bash
pip install -e '.[postgres]'       # PostgreSQL
pip install -e '.[sqlserver]'      # SQL Server
pip install -e '.[oracle]'         # Oracle
pip install -e '.[databases]'      # all three (required by the GMS profile)
```

## Configuration

Copy the example config and edit it before starting the server:

```bash
cp config.toml.example config.toml
```

```toml
[tools]
# all (default), core, gms, presentation, or minimal.
# Use enabled_categories for an exact override; see config.toml.example.
profile = "core"

[http]
# Streamable HTTP uses /mcp and binds to localhost by default.
host = "127.0.0.1"
port = 8080
allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
allowed_origins = ["http://127.0.0.1:*", "http://localhost:*"]
# The token value comes from this environment variable, never from TOML.
bearer_token_env = "MCP_HTTP_BEARER_TOKEN"

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

[export]
# Directory for large query results (db_query_to_file,
# gms_history_values(to_file=true)). Must already exist.
# Automatically added to allowed_paths. Files older than 7 days are
# cleaned up automatically.
dir = "/home/user/data/mcp_exports"
# Optional: serve exported CSVs over HTTP so a *different* machine's MCP
# server can download them by URL instead of needing local filesystem
# access. When enabled, advertise_host (this machine's address as seen by
# the other machine) is required. See config.toml.example for all fields
# (download_host, download_port, url_ttl_minutes) and the security model.
serve_downloads = false
```

Tool profiles reduce the number of schemas sent to the model. The default
`all` profile preserves the previous behavior. `core` enables filesystem,
database, custom, and API tools; `gms` and `presentation` enable only that
domain plus custom utilities; `minimal` exposes custom utilities only.

You can point to a custom config location with the `MCP_CONFIG` environment variable:

```bash
MCP_CONFIG=/etc/mcp/config.toml python -m mcp_server.server
```

## Running the Server

```bash
# stdio transport (for Claude Desktop, MCP Inspector)
python -m mcp_server.server

# Streamable HTTP transport (endpoint: http://127.0.0.1:8080/mcp)
python -m mcp_server.server --transport streamable-http
```

Binding Streamable HTTP beyond localhost requires a strong bearer token in
`MCP_HTTP_BEARER_TOKEN`, plus matching `allowed_hosts` and (for browser clients)
`allowed_origins`; clients send it as `Authorization: Bearer <token>`. Legacy
`sse` remains available only on localhost for client migration and logs a
deprecation warning.

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

For Open WebUI versions with Streamable HTTP support:

1. Start the server in Streamable HTTP mode:
   ```bash
   python -m mcp_server.server --transport streamable-http --port 8080
   ```
2. In Open WebUI → **Settings → Tools** → add a new tool server:
   - URL: `http://localhost:8080/mcp`
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
| `list_directory(path, recursive, limit, cursor)` | List directory entries with metadata using bounded pages |
| `search_files(directory, pattern, recursive, limit, cursor)` | Glob-search for files using bounded pages |
| `file_info(path)` | Get file/directory metadata |
| `delete_file(path)` | Delete a file — requires `allow_write = true` |

### Database (SQLite)

Tools use a `db_name` alias from `config.toml` instead of a raw file path. Call `db_list_databases()` first to see what's available.

| Tool | Description |
|------|-------------|
| `db_list_databases()` | List configured database names as `{databases, count}` |
| `db_query(db_name, sql, params, limit, cursor)` | SELECT query, returns a bounded page of row dicts |
| `db_execute(db_name, sql, params)` | INSERT / UPDATE / DELETE |
| `db_list_schemas(db_name, limit, cursor)` | List schemas using bounded pages |
| `db_list_tables(db_name, schema, limit, cursor)` | List tables using bounded pages |
| `db_table_schema(db_name, table_name)` | Get column definitions as `{columns, count}` |
| `db_execute_script(db_name, script)` | Run a multi-statement SQL script |
| `db_query_to_file(db_name, sql, params, filename)` | Streams the full result to CSV in bounded batches and returns only `{path, columns, row_count, preview (first 5 rows), size_kb}` — use for large result sets instead of paging through `db_query`. When `[export] serve_downloads` is enabled, the result also includes a time-limited `download_url` for a different machine's MCP server to stream the CSV over HTTP |

### GMS (compressed-air point/tag/value queries)

Domain-specific tools that encode the fixed query logic for compressed-air
equipment: PostgreSQL schema prefixes, Oracle zone/system table naming
(`FACCIMTAB.ZONE{1|2}_{building}_{GMS|PMS}`), >10-tag batching, and a 1-day
history cap. They read from the same connections as `db_query` (catalog:
`postgreSQL_CIM`, realtime: `oracle`, configured under
`[database.connections]`) — prefer these over hand-written SQL for
compressed-air queries; fall back to `db_query` for anything ad-hoc.

| Tool | Description |
|------|-------------|
| `gms_list_equipment(..., limit, cursor)` | List equipment from the PostgreSQL master (all filters optional) using bounded pages |
| `gms_list_points(..., limit, cursor)` | List monitoring points/tags for one device using bounded pages; category/type filters disambiguate duplicate device IDs |
| `gms_list_pipe_points(..., limit, cursor)` | List pipe-network points (HCDA/LCDA/HVAC) using bounded pages |
| `gms_realtime_values(..., limit, cursor)` | Latest SCADA values for already-known tags using bounded pages |
| `gms_history_values(..., to_file, limit, cursor)` | Historical samples, clamped to one day. Inline mode returns paged sample `items` plus per-tag `summaries`; file mode writes the complete series to CSV |
| `gms_history_aggregate(..., bucket, aggs, to_file, limit, cursor)` | Oracle-side downsampled history. Inline buckets are paged; file mode writes all buckets to CSV |

All bounded inline tools return `{items, count, truncated, next_cursor}`. The
default `limit` is 100 and the maximum is 1000. When `truncated` is true, call
the same tool with the same filters and `cursor=next_cursor`. Cursors are bound
to the original tool arguments and cannot be reused with a different query.

### API (external HTTP)

Services are configured under `[api.services]` in `config.toml`. Tools use a
`service` name alias; the `base_url` and `api_key` are injected server-side and
never exposed to the model. Call `api_list_services()` first to see what's available.

| Tool | Description |
|------|-------------|
| `api_list_services()` | List configured API service names as `{services, count}` |
| `api_request(service, method, path, query, json_body)` | Make an HTTP request to a named service; returns `{status, body}` |
| `push_notify(service, title, content, image_path, push_to_list)` | Send a Push+ notification; fills the template's `$_title` / `$_content` (content may be inline HTML). `image_path` embeds an image file as inline base64 — the server encodes it, so you never paste base64 yourself |

The `token`/`api_key` is read from the service's `config.toml` block and never exposed to the model. For an internal service whose TLS certificate is not publicly trusted, set `verify = false` in its service block to skip certificate verification.

Generic services default to GET-only. Configure `allowed_methods` and narrow
`allowed_path_prefixes` per service before enabling writes; `timeout_seconds`
(maximum 120) and `max_request_body_bytes` (maximum 1 MB) bound each request.

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

The default image contains the SQLite-only core. Set `MCP_EXTRAS` when the
deployment uses another database, for example:

```bash
MCP_EXTRAS=oracle docker compose build
MCP_HTTP_BEARER_TOKEN='replace-with-at-least-32-characters' docker compose up -d
```

The container mounts `./config.toml` as read-only at `/config/config.toml` and
persists data in a named volume `mcp-data`. To write logs to a file, set
`MCP_LOG_FILE=/data/mcp_server.log` in `docker-compose.yml`.

### systemd (Linux bare-metal / VM)

```bash
# Run once as root — creates service user, installs to /opt/mcp-server,
# copies config template to /etc/mcp/config.toml, and enables the service.
sudo bash deploy/install-systemd.sh
# Or, for Oracle: sudo env MCP_EXTRAS=oracle bash deploy/install-systemd.sh

# Edit the config before starting
sudo nano /etc/mcp/config.toml

sudo systemctl start mcp-server
sudo systemctl status mcp-server
journalctl -u mcp-server -f      # live logs
```

### Offline / air-gapped (Windows)

For a target machine with no internet access, pack the dependencies on an
online machine and install from the bundle on the target. Both machines must
run the **same OS and Python version** (wheels are platform-specific).

```powershell
# 1. On the ONLINE dev machine (code already on the latest main):
git pull
.\scripts\pack_offline.ps1        # SQLite-only; produces ..\mcp-server-offline.zip
# Or include drivers: .\scripts\pack_offline.ps1 -Extras databases

# 2. Copy the zip to the OFFLINE target, unzip it, then:
.\scripts\install_offline.ps1     # creates .venv, installs all wheels
copy config.toml.example config.toml   # then edit config.toml
```

`pack_offline.ps1` reads core dependencies and the selected database extra
straight from `pyproject.toml`; the offline installer automatically installs
the same selection.
`install_offline.ps1` points the venv at the live `src/` tree, so afterwards a
plain `git pull` (or unzipping a newer bundle) updates the server with no
reinstall — unless `pyproject.toml` dependencies changed, in which case re-run
`pack_offline.ps1` to refresh the bundle.

### Logging

Logging is configured with environment variables (not config.toml):

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `MCP_LOG_FILE`  | _(none)_ | If set, logs are also written to this file |

Logs always go to **stderr** to keep stdout clean for the stdio transport.
Write operations (`write_file`, `delete_file`, `db_execute`, `db_execute_script`)
are logged at INFO level for auditing.

Database aliases are read-only by default. Enable writes explicitly per alias;
script execution remains a separate opt-in:

```toml
[database.access.mydb]
read_only = false
allow_scripts = false
```

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
├── scripts/
│   ├── generate_pptx.js        # pptxgenjs renderer invoked by presentation.py
│   ├── setup_presentation.ps1  # one-time pptxgenjs/node_modules install
│   ├── download_pptxgenjs.py
│   ├── download_lucide_icons.py
│   ├── icons/                  # bundled Lucide SVG icon set
│   ├── pack_offline.ps1        # build the offline install bundle
│   └── install_offline.ps1     # install the offline bundle on the target machine
├── .claude/
│   └── commands/
│       └── create-presentation.md  # guided presentation-creation skill
├── docs/
│   └── HANDOFF.md              # maintainer handoff document
├── tests/                      # pure-logic unit tests (pytest)
├── src/
│   └── mcp_server/
│       ├── config.py           # Access control, config loader & startup validation
│       ├── server.py           # Entry point + CLI args
│       ├── tools/
│       │   ├── filesystem.py
│       │   ├── database.py
│       │   ├── gms.py          # compressed-air point/tag/value queries
│       │   ├── api.py
│       │   ├── presentation.py # pptx generation via pptxgenjs
│       │   └── custom.py
│       └── utils/
│           ├── errors.py
│           ├── export.py       # CSV export helpers (filename, cleanup)
│           ├── download_server.py # read-only HTTP server for cross-machine CSV downloads
│           └── logging.py      # Structured logging setup
└── README.md
```
