"""Tests for database parsing, pooling, and write-access safeguards."""

import sys
import sqlite3
import types

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


# ── Oracle call_timeout: no statement timeout of its own otherwise ───────
# Oracle has no server-side statement timeout. Without call_timeout set on
# the connection, a query scanning far more rows than intended (e.g. a wide
# gms_history_aggregate tag/date range) blocks until the DB responds or the
# MCP client's own request timeout gives up.

class _FakeOracleCursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOracleConnection:
    def __init__(self):
        self.call_timeout = "unset"

    def cursor(self):
        return _FakeOracleCursor()


def _install_fake_oracledb(monkeypatch):
    connected: list[_FakeOracleConnection] = []

    def fake_connect(**kwargs):
        conn = _FakeOracleConnection()
        connected.append(conn)
        return conn

    fake_module = types.SimpleNamespace(connect=fake_connect, Error=Exception)
    monkeypatch.setitem(sys.modules, "oracledb", fake_module)
    return connected


def test_oracle_conn_applies_configured_call_timeout(monkeypatch):
    database.close_all_pools()
    connected = _install_fake_oracledb(monkeypatch)
    monkeypatch.setattr(cfg, "get_oracle_call_timeout_ms", lambda: 45000)

    with database._oracle_conn("oracle://u:p@host:1521/svc1", cfg):
        pass

    assert connected[0].call_timeout == 45000
    database.close_all_pools()


def test_oracle_conn_skips_call_timeout_when_disabled(monkeypatch):
    database.close_all_pools()
    connected = _install_fake_oracledb(monkeypatch)
    monkeypatch.setattr(cfg, "get_oracle_call_timeout_ms", lambda: None)

    with database._oracle_conn("oracle://u:p@host:1521/svc2", cfg):
        pass

    assert connected[0].call_timeout == "unset"
    database.close_all_pools()


# ── database.oracle_call_timeout_seconds config ───────────────────────────

def test_get_oracle_call_timeout_ms_converts_seconds(monkeypatch):
    monkeypatch.setattr(cfg, "_oracle_call_timeout_seconds", 30)
    assert cfg.get_oracle_call_timeout_ms() == 30000


def test_get_oracle_call_timeout_ms_zero_disables(monkeypatch):
    monkeypatch.setattr(cfg, "_oracle_call_timeout_seconds", 0)
    assert cfg.get_oracle_call_timeout_ms() is None


def test_invalid_oracle_call_timeout_is_rejected(monkeypatch):
    monkeypatch.setattr(cfg, "_tools_cfg", {})
    monkeypatch.setattr(cfg, "_tool_profile", "all")
    monkeypatch.setattr(cfg, "_enabled_categories", None)
    monkeypatch.setattr(cfg, "_oracle_call_timeout_seconds", -5)
    with pytest.raises(cfg.ConfigError, match="oracle_call_timeout_seconds"):
        cfg.validate_config()


def test_oracle_call_timeout_true_is_rejected(monkeypatch):
    # bool is a subclass of int in Python — `oracle_call_timeout_seconds = true`
    # in TOML must not silently pass isinstance(int) and become a 1-second timeout.
    monkeypatch.setattr(cfg, "_tools_cfg", {})
    monkeypatch.setattr(cfg, "_tool_profile", "all")
    monkeypatch.setattr(cfg, "_enabled_categories", None)
    monkeypatch.setattr(cfg, "_oracle_call_timeout_seconds", True)
    with pytest.raises(cfg.ConfigError, match="oracle_call_timeout_seconds"):
        cfg.validate_config()


def test_oracle_call_timeout_false_is_accepted_as_disable(monkeypatch):
    # The error message documents "0/false to disable" — false must actually
    # pass validation, not just 0.
    monkeypatch.setattr(cfg, "_tools_cfg", {})
    monkeypatch.setattr(cfg, "_tool_profile", "all")
    monkeypatch.setattr(cfg, "_enabled_categories", None)
    monkeypatch.setattr(cfg, "_oracle_call_timeout_seconds", False)
    cfg.validate_config()  # must not raise


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


class _FakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_pool_close_closes_idle_connections():
    pool = _ConnPool(_FakeConnection, max_size=2)
    conn = pool.acquire()
    pool.release(conn, healthy=True)

    assert pool.close() == 1
    assert conn.closed is True
    with pytest.raises(RuntimeError, match="pool is closed"):
        pool.acquire()


def test_pool_close_closes_borrowed_connection_when_released():
    pool = _ConnPool(_FakeConnection, max_size=1)
    conn = pool.acquire()

    assert pool.close() == 0
    assert conn.closed is False
    pool.release(conn, healthy=True)
    assert conn.closed is True


def test_close_all_pools_closes_and_unregisters_connections():
    database.close_all_pools()
    conn = _FakeConnection()
    pool = _ConnPool(lambda: conn, max_size=1)
    database._pools["postgresql://test"] = pool
    pool.release(pool.acquire(), healthy=True)

    database.close_all_pools()

    assert conn.closed is True
    assert database._pools == {}


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
