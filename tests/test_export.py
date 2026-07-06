"""Tests for the file-based query result export path.

Covers: mcp_server.utils.export (CSV/filename/cleanup helpers), the
db_query_to_file tool (SQLite, no real Postgres/Oracle needed), and the
gms_history_values to_file parameter (Oracle/PostgreSQL calls monkeypatched).
"""

import csv
import os
import sqlite3
import time
from datetime import datetime

import pytest
from mcp.server.fastmcp import FastMCP

import mcp_server.config as cfg
from mcp_server.tools import database, gms
from mcp_server.utils import export as export_utils


def _get_tool(mcp: FastMCP, name: str):
    return mcp._tool_manager.get_tool(name).fn


# ── export_utils.sanitize_filename ──────────────────────────────────────────

def test_sanitize_filename_normal():
    assert export_utils.sanitize_filename("myquery", "csv") == "myquery.csv"


def test_sanitize_filename_strips_extension_and_appends_target():
    assert export_utils.sanitize_filename("myquery.txt", "csv") == "myquery.csv"


def test_sanitize_filename_strips_path_separators():
    assert export_utils.sanitize_filename("../../etc/passwd", "csv") == "passwd.csv"


def test_sanitize_filename_replaces_unsafe_chars():
    result = export_utils.sanitize_filename("weird:name?.txt", "csv")
    assert result.endswith(".csv")
    assert "/" not in result and ":" not in result and "?" not in result


def test_sanitize_filename_empty_falls_back_to_timestamp():
    name = export_utils.sanitize_filename("", "csv")
    assert name.startswith("query_")
    assert name.endswith(".csv")


def test_sanitize_filename_only_unsafe_chars_falls_back_to_timestamp():
    name = export_utils.sanitize_filename("???", "csv")
    assert name.startswith("query_")
    assert name.endswith(".csv")


# ── export_utils.write_csv ──────────────────────────────────────────────────

def test_write_csv_uses_utf8_sig_bom_and_chinese_content(tmp_path):
    path = tmp_path / "out.csv"
    export_utils.write_csv(path, ["name", "value"], [{"name": "溫度", "value": 25.5}])
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # utf-8-sig BOM
    text = path.read_text(encoding="utf-8-sig")
    assert "溫度" in text
    assert "25.5" in text


# ── export_utils.cleanup_old_exports ────────────────────────────────────────

def test_cleanup_old_exports_deletes_old_keeps_new(tmp_path):
    old_file = tmp_path / "old.csv"
    new_file = tmp_path / "new.csv"
    old_file.write_text("old")
    new_file.write_text("new")

    eight_days_ago = time.time() - 8 * 86400
    os.utime(old_file, (eight_days_ago, eight_days_ago))

    export_utils.cleanup_old_exports(tmp_path)

    assert not old_file.exists()
    assert new_file.exists()


def test_cleanup_old_exports_ignores_non_matching_extensions(tmp_path):
    other = tmp_path / "old.txt"
    other.write_text("old")
    eight_days_ago = time.time() - 8 * 86400
    os.utime(other, (eight_days_ago, eight_days_ago))

    export_utils.cleanup_old_exports(tmp_path)

    assert other.exists()  # only *.csv is cleaned up


# ── db_query_to_file ─────────────────────────────────────────────────────

@pytest.fixture
def sqlite_db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
    conn.executemany(
        "INSERT INTO t (id, name) VALUES (?, ?)",
        [(i, f"row{i}") for i in range(7)],
    )
    conn.commit()
    conn.close()
    return db_path


def test_db_query_to_file_full_flow(monkeypatch, tmp_path, sqlite_db):
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.setattr(cfg, "resolve_db", lambda name: str(sqlite_db))
    monkeypatch.setattr(cfg, "get_export_dir", lambda: export_dir)

    mcp = FastMCP(name="test")
    database.register(mcp, cfg)
    db_query_to_file = _get_tool(mcp, "db_query_to_file")

    result = db_query_to_file(db_name="mydb", sql="SELECT id, name FROM t ORDER BY id")

    assert result["row_count"] == 7
    assert result["columns"] == ["id", "name"]
    assert len(result["preview"]) == 5
    assert result["preview"][0] == {"id": 0, "name": "row0"}
    assert result["size_kb"] > 0

    out_path = export_dir / os.path.basename(result["path"])
    assert out_path.exists()
    with out_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 7
    assert rows[0] == {"id": "0", "name": "row0"}


def test_db_query_to_file_custom_filename(monkeypatch, tmp_path, sqlite_db):
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.setattr(cfg, "resolve_db", lambda name: str(sqlite_db))
    monkeypatch.setattr(cfg, "get_export_dir", lambda: export_dir)

    mcp = FastMCP(name="test")
    database.register(mcp, cfg)
    db_query_to_file = _get_tool(mcp, "db_query_to_file")

    result = db_query_to_file(db_name="mydb", sql="SELECT id, name FROM t", filename="myreport")

    assert result["path"].endswith("myreport.csv")


def test_db_query_to_file_rejects_non_select(monkeypatch, tmp_path, sqlite_db):
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.setattr(cfg, "resolve_db", lambda name: str(sqlite_db))
    monkeypatch.setattr(cfg, "get_export_dir", lambda: export_dir)

    mcp = FastMCP(name="test")
    database.register(mcp, cfg)
    db_query_to_file = _get_tool(mcp, "db_query_to_file")

    from mcp_server.utils.errors import ToolError
    with pytest.raises(ToolError):
        db_query_to_file(db_name="mydb", sql="DELETE FROM t")


# ── gms_history_values(to_file=...) ─────────────────────────────────────────

_FAKE_POINTS = [
    {
        "point_seq": 1, "point_name": "壓力", "phase": "", "unit": "kg/cm2",
        "tag_name": "K18_GMS_A1_PRESSURE", "scada_available": True, "remark": None,
    },
    {
        "point_seq": 2, "point_name": "溫度", "phase": "", "unit": "C",
        "tag_name": "K18_GMS_A1_TEMP", "scada_available": True, "remark": None,
    },
]

_FAKE_HISTORY_ROWS = [
    {"TAGNAME": "K18_GMS_A1_PRESSURE", "VALUE": 1.1, "DATETIME": datetime(2026, 7, 3, 12, 0, 0)},
    {"TAGNAME": "K18_GMS_A1_PRESSURE", "VALUE": 1.2, "DATETIME": datetime(2026, 7, 3, 12, 5, 0)},
    {"TAGNAME": "K18_GMS_A1_TEMP", "VALUE": 30.0, "DATETIME": datetime(2026, 7, 3, 12, 0, 0)},
]


@pytest.fixture
def gms_mcp(monkeypatch):
    monkeypatch.setattr(cfg, "resolve_db", lambda name: "dummy-dsn")
    monkeypatch.setattr(gms, "_fetch_points_by_tags", lambda cfg, building, tag_names: _FAKE_POINTS)
    monkeypatch.setattr(
        gms, "_oracle_history",
        lambda cfg, dsn, table, tags, start, end: [
            row for row in _FAKE_HISTORY_ROWS if row["TAGNAME"] in tags
        ],
    )
    mcp = FastMCP(name="test")
    gms.register(mcp, cfg)
    return mcp


def test_gms_history_values_to_file_false_unchanged_format(gms_mcp):
    gms_history_values = _get_tool(gms_mcp, "gms_history_values")
    import json

    raw = gms_history_values(
        building="K18",
        start_time="2026-07-03 00:00:00",
        end_time="2026-07-03 23:59:59",
        tag_names=["K18_GMS_A1_PRESSURE", "K18_GMS_A1_TEMP"],
    )
    result = json.loads(raw)

    assert "file" not in result
    assert set(result.keys()) == {"adjusted", "start_time", "end_time", "points"}
    for point in result["points"]:
        assert "series" in point
        assert "summary" in point
    pressure = next(p for p in result["points"] if p["tag_name"] == "K18_GMS_A1_PRESSURE")
    assert len(pressure["series"]) == 2
    assert pressure["summary"] == {"max": 1.2, "min": 1.1, "latest": 1.2}


def test_gms_history_values_to_file_true_omits_series_includes_file(gms_mcp, monkeypatch, tmp_path):
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.setattr(cfg, "get_export_dir", lambda: export_dir)

    gms_history_values = _get_tool(gms_mcp, "gms_history_values")
    import json

    raw = gms_history_values(
        building="K18",
        start_time="2026-07-03 00:00:00",
        end_time="2026-07-03 23:59:59",
        tag_names=["K18_GMS_A1_PRESSURE", "K18_GMS_A1_TEMP"],
        to_file=True,
    )
    result = json.loads(raw)

    assert "file" in result
    for point in result["points"]:
        assert "series" not in point
        assert "summary" in point

    file_info = result["file"]
    assert file_info["row_count"] == 3
    out_path = export_dir / os.path.basename(file_info["path"])
    assert out_path.exists()
    with out_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
    assert {r["tag_name"] for r in rows} == {"K18_GMS_A1_PRESSURE", "K18_GMS_A1_TEMP"}
