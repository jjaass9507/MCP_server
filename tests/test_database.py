"""Tests for database parsing, pooling, and write-access safeguards."""

import sqlite3

import pytest
from mcp.server.fastmcp import FastMCP

import mcp_server.config as cfg
from mcp_server.tools import database
from mcp_server.tools.database import (
    _ConnPool,
    _parse_mssql_dsn,
    _parse_oracle_dsn,
    _sqlite_conn,
    _write_operation,
)
from mcp_server.utils.errors import ToolError


def _get_tool(mcp: FastMCP, name: str):
    return mcp._tool_manager.get_tool(name).fn


# ── _parse_mssql_dsn ─────────────────────────────────────────────────────

def test_parse_mssql_dsn_normal():
    result = _parse_mssql_dsn("mssql://user:secret@dbhost:1433/mydb")
    assert result == {
        "server": "dbhost",
        "port": "1433",
        "user": "user",
        "password": "secret",
        "database": "mydb",
    }


def test_parse_mssql_dsn_default_port():
    result = _parse_mssql_dsn("mssql://user:secret@dbhost/mydb")
    assert result["port"] == "1433"


def test_parse_mssql_dsn_missing_host_raises():
    with pytest.raises(ToolError):
        _parse_mssql_dsn("mssql:///mydb")


def test_parse_mssql_dsn_url_encoded_password():
    result = _parse_mssql_dsn("mssql://user:p%40ss%23word@dbhost:1433/mydb")
    assert result["password"] == "p@ss#word"


# ── _parse_oracle_dsn ────────────────────────────────────────────────────

def test_parse_oracle_dsn_normal():
    result = _parse_oracle_dsn("oracle://user:secret@dbhost:1521/orclservice")
    assert result == {
        "user": "user",
        "password": "secret",
        "dsn": "dbhost:1521/orclservice",
    }


def test_parse_oracle_dsn_default_port():
    result = _parse_oracle_dsn("oracle://user:secret@dbhost/orclservice")
    assert result["dsn"] == "dbhost:1521/orclservice"


def test_parse_oracle_dsn_missing_host_raises():
    with pytest.raises(ToolError):
        _parse_oracle_dsn("oracle:///orclservice")


def test_parse_oracle_dsn_missing_service_raises():
    with pytest.raises(ToolError):
        _parse_oracle_dsn("oracle://user:secret@dbhost:1521/")


def test_parse_oracle_dsn_url_encoded_password():
    result = _parse_oracle_dsn("oracle://user:p%40ss%23word@dbhost:1521/orcl")
    assert result["password"] == "p@ss#word"


# ── connection safety ────────────────────────────────────────────────────

def test_pool_connect_failure_releases_capacity():
    attempts = 0

    def connect():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("connection failed")
        return object()

    pool = _ConnPool(connect, max_size=1)
    with pytest.raises(RuntimeError, match="connection failed"):
        pool.acquire()

    assert pool.acquire() is not None
    assert attempts == 2


def test_sqlite_connect_error_preserves_original_error(monkeypatch):
    def fail_connect(_path):
        raise sqlite3.OperationalError("cannot open database")

    monkeypatch.setattr(sqlite3, "connect", fail_connect)
    with pytest.raises(ToolError, match="cannot open database"):
        with _sqlite_conn("missing.sqlite"):
            pass


# ── write policy ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("sql", ["INSERT INTO t VALUES (1)", "update t set x=1;", "DELETE FROM t"])
def test_write_operation_allows_supported_single_statements(sql):
    assert _write_operation(sql) in {"INSERT", "UPDATE", "DELETE"}


@pytest.mark.parametrize(
    "sql",
    ["", "SELECT * FROM t", "DROP TABLE t", "PRAGMA journal_mode=WAL", "INSERT INTO t VALUES (1); DROP TABLE t"],
)
def test_write_operation_rejects_other_or_multiple_statements(sql):
    with pytest.raises(ToolError):
        _write_operation(sql)


def test_db_write_is_read_only_by_default(monkeypatch):
    monkeypatch.setattr(cfg, "_db_access", {})
    with pytest.raises(ToolError, match="read-only"):
        cfg.check_db_write("mydb")


def test_db_scripts_require_separate_opt_in(monkeypatch):
    monkeypatch.setattr(
        cfg, "_db_access", {"mydb": {"read_only": False, "allow_scripts": False}}
    )
    cfg.check_db_write("mydb")
    with pytest.raises(ToolError, match="scripts are disabled"):
        cfg.check_db_write("mydb", script=True)


def test_db_execute_enforces_alias_policy_before_writing(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE items (value INTEGER)")

    monkeypatch.setattr(cfg, "list_db_names", lambda: ["mydb"])
    monkeypatch.setattr(cfg, "resolve_db", lambda _name: str(db_path))
    monkeypatch.setattr(cfg, "_db_access", {})
    mcp = FastMCP(name="test")
    database.register(mcp, cfg)
    execute = _get_tool(mcp, "db_execute")

    with pytest.raises(ToolError, match="read-only"):
        execute("mydb", "INSERT INTO items VALUES (1)")
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0

    monkeypatch.setattr(cfg, "_db_access", {"mydb": {"read_only": False}})
    assert execute("mydb", "INSERT INTO items VALUES (1)")["rows_affected"] == 1
