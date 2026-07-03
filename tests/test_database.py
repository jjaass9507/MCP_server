"""Pure-logic tests for mcp_server.tools.database DSN parsing — no DB required."""

import pytest

from mcp_server.tools.database import _parse_mssql_dsn, _parse_oracle_dsn
from mcp_server.utils.errors import ToolError


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
