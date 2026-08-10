"""Pagination contract tests for bounded inline tool responses."""

import sqlite3

import pytest
from mcp.server.fastmcp import FastMCP

import mcp_server.config as cfg
from mcp_server.tools import database, filesystem
from mcp_server.utils.errors import ToolError
from mcp_server.utils.pagination import MAX_LIMIT, paginate


def _get_tool(mcp: FastMCP, name: str):
    return mcp._tool_manager.get_tool(name).fn


def test_paginate_rejects_invalid_limits_and_cursor():
    for limit in (0, MAX_LIMIT + 1, True):
        with pytest.raises(ToolError, match="limit"):
            paginate([1, 2], limit, scope="test")
    with pytest.raises(ToolError, match="Invalid cursor"):
        paginate([1, 2], cursor="not-a-cursor", scope="test")


def test_cursor_is_bound_to_original_call():
    first = paginate([1, 2, 3], 1, scope="query-a")
    with pytest.raises(ToolError, match="Invalid cursor"):
        paginate([1, 2, 3], 1, first["next_cursor"], scope="query-b")


def test_db_query_pages_without_returning_all_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "page.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        conn.executemany(
            "INSERT INTO items VALUES (?, ?)",
            [(i, f"row-{i}") for i in range(5)],
        )

    monkeypatch.setattr(cfg, "list_db_names", lambda: ["test"])
    monkeypatch.setattr(cfg, "resolve_db", lambda _name: str(db_path))
    mcp = FastMCP(name="test")
    database.register(mcp, cfg)
    query = _get_tool(mcp, "db_query")

    first = query(sql="SELECT id, name FROM items ORDER BY id", limit=2)
    assert [row["id"] for row in first["items"]] == [0, 1]
    assert first["count"] == 2
    assert first["truncated"] is True

    second = query(
        sql="SELECT id, name FROM items ORDER BY id",
        limit=2,
        cursor=first["next_cursor"],
    )
    assert [row["id"] for row in second["items"]] == [2, 3]
    assert second["truncated"] is True

    final = query(
        sql="SELECT id, name FROM items ORDER BY id",
        limit=2,
        cursor=second["next_cursor"],
    )
    assert [row["id"] for row in final["items"]] == [4]
    assert final["truncated"] is False
    assert final["next_cursor"] is None


def test_db_query_cursor_cannot_be_reused_for_different_sql(tmp_path, monkeypatch):
    db_path = tmp_path / "scope.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE items (id INTEGER)")
        conn.executemany("INSERT INTO items VALUES (?)", [(1,), (2,)])

    monkeypatch.setattr(cfg, "list_db_names", lambda: ["test"])
    monkeypatch.setattr(cfg, "resolve_db", lambda _name: str(db_path))
    mcp = FastMCP(name="test")
    database.register(mcp, cfg)
    query = _get_tool(mcp, "db_query")
    first = query(sql="SELECT id FROM items ORDER BY id", limit=1)

    with pytest.raises(ToolError, match="Invalid cursor"):
        query(
            sql="SELECT id FROM items ORDER BY id DESC",
            limit=1,
            cursor=first["next_cursor"],
        )


def test_filesystem_list_and_search_are_paginated(tmp_path, monkeypatch):
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    monkeypatch.setattr(cfg, "check_path", lambda _path, write=False: None)
    mcp = FastMCP(name="test")
    filesystem.register(mcp, cfg)
    list_directory = _get_tool(mcp, "list_directory")
    search_files = _get_tool(mcp, "search_files")

    first = list_directory(str(tmp_path), limit=2)
    assert [item["name"] for item in first["items"]] == ["a.txt", "b.txt"]
    assert first["truncated"] is True
    second = list_directory(
        str(tmp_path), limit=2, cursor=first["next_cursor"]
    )
    assert [item["name"] for item in second["items"]] == ["c.txt"]
    assert second["truncated"] is False

    matches = search_files(str(tmp_path), "*.txt", limit=1)
    assert matches["count"] == 1
    assert matches["truncated"] is True
