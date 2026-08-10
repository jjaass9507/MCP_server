"""Contract tests for native MCP structured tool results."""

import asyncio
import sqlite3
from pathlib import Path

from mcp.server.fastmcp import FastMCP

import mcp_server.config as cfg
from mcp_server.tools import api, custom, database, filesystem, gms


def _get_tool(mcp: FastMCP, name: str):
    return mcp._tool_manager.get_tool(name)


def test_non_presentation_object_tools_publish_output_schemas():
    mcp = FastMCP(name="test")
    filesystem.register(mcp, cfg)
    database.register(mcp, cfg)
    custom.register(mcp)
    api.register(mcp, cfg)
    gms.register(mcp, cfg)

    structured_tools = {
        "fs_list_allowed_paths",
        "list_directory",
        "search_files",
        "file_info",
        "db_list_databases",
        "db_query",
        "db_query_to_file",
        "db_execute",
        "db_list_schemas",
        "db_list_tables",
        "db_table_schema",
        "system_info",
        "api_list_services",
        "api_request",
        "push_notify",
        "gms_list_equipment",
        "gms_list_points",
        "gms_list_pipe_points",
        "gms_realtime_values",
        "gms_history_values",
        "gms_history_aggregate",
    }

    for name in structured_tools:
        tool = _get_tool(mcp, name)
        assert tool.fn_metadata.output_schema is not None, name
        assert tool.fn_metadata.output_schema["type"] == "object", name


def test_discovery_tools_return_named_structured_collections(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "list_db_names", lambda: ["primary", "archive"])
    monkeypatch.setattr(cfg, "list_api_names", lambda: ["weather", "alerts"])
    monkeypatch.setattr(cfg, "_allowed_paths", [Path(tmp_path)])

    mcp = FastMCP(name="test")
    filesystem.register(mcp, cfg)
    database.register(mcp, cfg)
    api.register(mcp, cfg)

    assert _get_tool(mcp, "db_list_databases").fn() == {
        "databases": ["primary", "archive"],
        "count": 2,
    }
    assert _get_tool(mcp, "api_list_services").fn() == {
        "services": ["weather", "alerts"],
        "count": 2,
    }
    assert _get_tool(mcp, "fs_list_allowed_paths").fn() == {
        "paths": [str(tmp_path)],
        "count": 1,
    }


def test_db_table_schema_returns_columns_object(tmp_path, monkeypatch):
    db_path = tmp_path / "schema.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )

    monkeypatch.setattr(cfg, "list_db_names", lambda: ["primary"])
    monkeypatch.setattr(cfg, "resolve_db", lambda _name: str(db_path))

    mcp = FastMCP(name="test")
    database.register(mcp, cfg)
    result = _get_tool(mcp, "db_table_schema").fn("primary", "items")

    assert result["count"] == 2
    assert [column["name"] for column in result["columns"]] == ["id", "name"]
    assert result["columns"][0]["is_primary_key"] is True


def test_fastmcp_emits_structured_content_for_discovery_tool(monkeypatch):
    monkeypatch.setattr(cfg, "list_db_names", lambda: ["primary"])
    mcp = FastMCP(name="test")
    database.register(mcp, cfg)

    converted = asyncio.run(
        mcp._tool_manager.call_tool(
            "db_list_databases", {}, context=None, convert_result=True
        )
    )

    _, structured_content = converted
    assert structured_content == {"databases": ["primary"], "count": 1}
