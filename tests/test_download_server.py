"""Tests for the cross-machine CSV download server.

Covers: utils/download_server.py (register/serve/expire/delete-then-serve,
URL shape) over a real HTTP server bound to an ephemeral port, and the
db_query_to_file tool integration (download_url present/absent depending on
[export] serve_downloads).
"""

import http.client
import socket
import sqlite3
import time

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

import mcp_server.config as cfg
from mcp_server.tools import database
from mcp_server.utils import download_server


def _get_tool(mcp: FastMCP, name: str):
    return mcp._tool_manager.get_tool(name).fn


def _free_port() -> int:
    """Find a free TCP port by binding to port 0 and reading it back."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _raw_get(host: str, port: int, path: str) -> int:
    """GET a literal path with no client-side URL normalization, so tests
    can send shapes like '/exports/../x' exactly as written."""
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", path)
        return conn.getresponse().status
    finally:
        conn.close()


@pytest.fixture
def running_server(monkeypatch):
    """Start a real download_server instance on an ephemeral port and tear
    it down afterwards, so tests don't leave a daemon thread running that
    could interfere with other tests (or bind conflicts across tests)."""
    port = _free_port()
    settings = {
        "serve_downloads": True,
        "download_host": "127.0.0.1",
        "advertise_host": "127.0.0.1",
        "download_port": port,
        "url_ttl_minutes": 60,
    }
    monkeypatch.setattr(cfg, "get_download_config", lambda: dict(settings))
    download_server.start_download_server()
    try:
        yield settings
    finally:
        download_server._httpd.shutdown()
        download_server._httpd.server_close()
        download_server._httpd = None
        with download_server._lock:
            download_server._registry.clear()


# ── register_file + GET ─────────────────────────────────────────────────────

def test_register_and_get_success(running_server, tmp_path):
    csv_path = tmp_path / "out.csv"
    csv_bytes = "﻿name,value\r\n溫度,25.5\r\n".encode("utf-8")
    csv_path.write_bytes(csv_bytes)

    url = download_server.register_file(csv_path)
    resp = httpx.get(url)

    assert resp.status_code == 200
    assert resp.content == csv_bytes
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert resp.headers["content-disposition"] == 'attachment; filename="out.csv"'
    assert resp.headers["content-length"] == str(len(csv_bytes))


def test_register_file_url_shape(running_server, tmp_path):
    csv_path = tmp_path / "a.csv"
    csv_path.write_text("x", encoding="utf-8")

    url = download_server.register_file(csv_path)
    port = running_server["download_port"]

    assert url.startswith(f"http://127.0.0.1:{port}/exports/")
    file_id = url.rsplit("/", 1)[-1]
    assert len(file_id) >= 16  # secrets.token_urlsafe(16) -> ~22 url-safe chars


# ── 404 cases ────────────────────────────────────────────────────────────────

def test_get_nonexistent_id_404(running_server):
    port = running_server["download_port"]
    resp = httpx.get(f"http://127.0.0.1:{port}/exports/doesnotexist")
    assert resp.status_code == 404


@pytest.mark.parametrize("path", ["/", "/exports/", "/exports/../x", "/other"])
def test_bad_paths_404(running_server, path):
    port = running_server["download_port"]
    status = _raw_get("127.0.0.1", port, path)
    assert status == 404


def test_ttl_expired_returns_404(running_server, tmp_path):
    csv_path = tmp_path / "out.csv"
    csv_path.write_text("a,b\n1,2\n", encoding="utf-8-sig")
    url = download_server.register_file(csv_path)
    file_id = url.rsplit("/", 1)[-1]

    with download_server._lock:
        path, _expires_at = download_server._registry[file_id]
        download_server._registry[file_id] = (path, time.time() - 10)

    resp = httpx.get(url)
    assert resp.status_code == 404


def test_file_deleted_after_register_returns_404(running_server, tmp_path):
    csv_path = tmp_path / "out.csv"
    csv_path.write_text("a,b\n1,2\n", encoding="utf-8-sig")
    url = download_server.register_file(csv_path)
    csv_path.unlink()

    resp = httpx.get(url)
    assert resp.status_code == 404


# ── db_query_to_file tool integration ───────────────────────────────────────

@pytest.fixture
def sqlite_db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
    conn.executemany(
        "INSERT INTO t (id, name) VALUES (?, ?)",
        [(i, f"row{i}") for i in range(3)],
    )
    conn.commit()
    conn.close()
    return db_path


def _register_db_query_to_file(monkeypatch, tmp_path, sqlite_db, download_cfg):
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.setattr(cfg, "resolve_db", lambda name: str(sqlite_db))
    monkeypatch.setattr(cfg, "get_export_dir", lambda: export_dir)
    monkeypatch.setattr(cfg, "get_download_config", lambda: download_cfg)

    mcp = FastMCP(name="test")
    database.register(mcp, cfg)
    return _get_tool(mcp, "db_query_to_file")


def test_db_query_to_file_no_download_url_when_disabled(monkeypatch, tmp_path, sqlite_db):
    db_query_to_file = _register_db_query_to_file(
        monkeypatch, tmp_path, sqlite_db,
        {
            "serve_downloads": False, "download_host": "0.0.0.0",
            "advertise_host": "", "download_port": 8081, "url_ttl_minutes": 60,
        },
    )

    result = db_query_to_file(db_name="mydb", sql="SELECT id, name FROM t")

    assert "download_url" not in result


def test_db_query_to_file_has_download_url_when_enabled(monkeypatch, tmp_path, sqlite_db):
    db_query_to_file = _register_db_query_to_file(
        monkeypatch, tmp_path, sqlite_db,
        {
            "serve_downloads": True, "download_host": "0.0.0.0",
            "advertise_host": "10.0.0.5", "download_port": 8081, "url_ttl_minutes": 60,
        },
    )

    result = db_query_to_file(db_name="mydb", sql="SELECT id, name FROM t")

    assert "download_url" in result
    assert result["download_url"].startswith("http://10.0.0.5:8081/exports/")
