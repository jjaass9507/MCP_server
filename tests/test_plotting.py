"""Tests for plot_csv — renders a small CSV and checks the PNG comes out."""

import csv

import pytest
from mcp.server.fastmcp import FastMCP

import mcp_server.config as cfg
from mcp_server.tools import plotting


def _get_tool(mcp: FastMCP, name: str):
    return mcp._tool_manager.get_tool(name).fn


@pytest.fixture
def sample_csv(tmp_path):
    path = tmp_path / "sample.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["datetime", "pressure", "temp"])
        writer.writerows([
            ("2026-07-03 00:00:00", 1.1, 30.0),
            ("2026-07-03 01:00:00", 1.3, 31.0),
            ("2026-07-03 02:00:00", 1.0, 29.5),
            ("2026-07-03 03:00:00", 1.5, 32.0),
        ])
    return path


@pytest.fixture
def plot_csv_tool(monkeypatch, tmp_path, sample_csv):
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.setattr(cfg, "check_path", lambda p, write=False: None)
    monkeypatch.setattr(cfg, "get_export_dir", lambda: export_dir)

    mcp = FastMCP(name="test")
    plotting.register(mcp, cfg)
    return _get_tool(mcp, "plot_csv"), export_dir


def test_plot_csv_line_chart(plot_csv_tool, sample_csv):
    plot_csv, export_dir = plot_csv_tool

    result = plot_csv(
        csv_path=str(sample_csv),
        chart_type="line",
        x_column="datetime",
        y_columns=["pressure", "temp"],
        output_filename="line_test",
    )

    out_path = export_dir / "line_test.png"
    assert out_path.exists()
    assert out_path.stat().st_size > 0
    assert result["path"] == str(out_path)
    assert result["columns_plotted"] == ["pressure", "temp"]
    assert result["size_kb"] > 0


def test_plot_csv_correlation_chart(plot_csv_tool, sample_csv):
    plot_csv, export_dir = plot_csv_tool

    result = plot_csv(
        csv_path=str(sample_csv),
        chart_type="correlation",
        output_filename="corr_test",
    )

    out_path = export_dir / "corr_test.png"
    assert out_path.exists()
    assert out_path.stat().st_size > 0
    assert set(result["columns_plotted"]) == {"pressure", "temp"}


def test_plot_csv_requires_x_and_y_for_line(plot_csv_tool, sample_csv):
    plot_csv, _ = plot_csv_tool
    from mcp_server.utils.errors import ToolError

    with pytest.raises(ToolError):
        plot_csv(csv_path=str(sample_csv), chart_type="line")
