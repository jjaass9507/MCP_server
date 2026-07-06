"""Chart generation for CSV files produced by db_query_to_file /
gms_history_values(to_file=True).

This is the "Python tool" half of the file-based export design (see
docs/HANDOFF.md §8): large results never enter the model's context — they
are written to CSV, and this module turns a CSV into a PNG chart, which can
then be attached to a push_notify() call via image_path. Typical flow:

    db_query_to_file(...) / gms_history_values(..., to_file=true)
        -> plot_csv(csv_path=<path from the previous call>, ...)
        -> push_notify(image_path=<path from plot_csv>, ...)

matplotlib uses the non-interactive Agg backend (set before importing
pyplot) since this server never has a display. A CJK font fallback list is
configured so Chinese column names/labels render instead of "tofu" boxes —
Microsoft JhengHei is only present on the Windows deployment target, so
falling back to it renders as plain boxes on this Linux dev box; that's
expected and must be checked on the actual Windows machine.
"""

import csv
import pathlib
from datetime import datetime
from typing import TYPE_CHECKING, Literal

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (must follow matplotlib.use)
import numpy as np  # noqa: E402  (matplotlib already depends on numpy)

from mcp.server.fastmcp import FastMCP

from mcp_server.utils import export as export_utils
from mcp_server.utils.errors import ToolError
from mcp_server.utils.logging import get_logger

if TYPE_CHECKING:
    import mcp_server.config as _CfgModule

logger = get_logger("plotting")

plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Noto Sans CJK TC", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

_DT_FMT = "%Y-%m-%d %H:%M:%S"


def _read_csv(path: pathlib.Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    if not rows:
        raise ToolError(f"CSV file has no data rows: {path}")
    return fieldnames, rows


def _floats(rows: list[dict], column: str) -> list[float]:
    try:
        return [float(row[column]) for row in rows]
    except KeyError as e:
        raise ToolError(f"Column '{column}' not found in CSV.") from e
    except ValueError as e:
        raise ToolError(f"Column '{column}' contains a non-numeric value: {e}") from e


def _is_numeric_column(rows: list[dict], column: str) -> bool:
    try:
        for row in rows:
            float(row[column])
        return True
    except (KeyError, ValueError, TypeError):
        return False


def _parse_x_axis(values: list[str]) -> list:
    """Parse x values as 'YYYY-MM-DD HH:MM:SS' datetimes; fall back to the
    raw strings (plotted as a categorical axis) if any value doesn't parse.
    """
    try:
        return [datetime.strptime(v, _DT_FMT) for v in values]
    except ValueError:
        return values


def register(mcp: FastMCP, cfg: "_CfgModule") -> None:

    @mcp.tool()
    def plot_csv(
        csv_path: str,
        chart_type: Literal["line", "scatter", "bar", "correlation"] = "line",
        x_column: str = "",
        y_columns: list[str] = [],
        output_filename: str = "",
        title: str = "",
    ) -> dict:
        """Render a chart from a CSV file and save it as a PNG.

        Use this to visualize data that was written to disk by
        db_query_to_file or gms_history_values(to_file=true) — never read a
        large CSV back into context just to describe it; plot it instead.
        The typical chain is: *_to_file -> plot_csv -> push_notify(image_path=...).

        chart_type:
            "line" / "scatter": x_column vs each of y_columns. If x_column's
                values parse as 'YYYY-MM-DD HH:MM:SS' they're plotted as a
                time axis; otherwise as a categorical axis.
            "bar": same data, always plotted as a categorical x-axis
                (grouped bars if multiple y_columns).
            "correlation": ignores x_column; plots a correlation heatmap
                across y_columns (or, if y_columns is empty, every numeric
                column in the CSV).

        csv_path must be inside an allowed directory (typically the export
        directory a previous *_to_file call wrote to). The output PNG is
        written to the same export directory and old exports (CSV/PNG older
        than 7 days) are cleaned up on each call.

        Returns {"path": str, "size_kb": float, "columns_plotted": [str]}.

        Args:
            csv_path:        Path to a CSV file, e.g. from db_query_to_file's
                              or gms_history_values(to_file=true)'s "path".
            chart_type:       One of "line" / "scatter" / "bar" / "correlation".
            x_column:         Column to use as the x-axis. Required for
                               line/scatter/bar; ignored for correlation.
            y_columns:        Column(s) to plot on the y-axis. Required for
                               line/scatter/bar; optional for correlation
                               (defaults to all numeric columns).
            output_filename:  Optional output filename (.png enforced,
                               unsafe characters stripped). Defaults to a
                               timestamped name if omitted.
            title:            Optional chart title.
        """
        p = pathlib.Path(csv_path).resolve()
        cfg.check_path(p)
        if not p.is_file():
            raise ToolError(f"CSV file not found: {csv_path}")

        fieldnames, rows = _read_csv(p)

        fig, ax = plt.subplots(figsize=(10, 5.5))

        if chart_type == "correlation":
            columns = y_columns or [c for c in fieldnames if _is_numeric_column(rows, c)]
            if len(columns) < 2:
                raise ToolError(
                    "Need at least 2 numeric columns for a correlation matrix "
                    f"(found: {columns})."
                )
            data = np.array([_floats(rows, c) for c in columns])
            corr = np.corrcoef(data)
            im = ax.imshow(corr, vmin=-1, vmax=1, cmap="coolwarm")
            ax.set_xticks(range(len(columns)))
            ax.set_xticklabels(columns, rotation=45, ha="right")
            ax.set_yticks(range(len(columns)))
            ax.set_yticklabels(columns)
            for i in range(len(columns)):
                for j in range(len(columns)):
                    ax.text(j, i, f"{corr[i, j]:.2f}", ha="center", va="center", fontsize=8)
            fig.colorbar(im, ax=ax)
            ax.set_title(title or "Correlation Matrix")
            columns_plotted = columns
        else:
            if not x_column or not y_columns:
                raise ToolError("x_column and y_columns are required for line/scatter/bar charts.")
            if x_column not in fieldnames:
                raise ToolError(f"x_column '{x_column}' not found in CSV columns: {fieldnames}")
            for c in y_columns:
                if c not in fieldnames:
                    raise ToolError(f"y_column '{c}' not found in CSV columns: {fieldnames}")

            x_raw = [row[x_column] for row in rows]
            y_data = {c: _floats(rows, c) for c in y_columns}

            if chart_type == "bar":
                positions = list(range(len(rows)))
                n = len(y_columns)
                width = 0.8 / n
                for i, c in enumerate(y_columns):
                    offset = (i - (n - 1) / 2) * width
                    ax.bar([pos + offset for pos in positions], y_data[c], width=width, label=c)
                ax.set_xticks(positions)
                ax.set_xticklabels(x_raw, rotation=45, ha="right")
            else:
                x_values = _parse_x_axis(x_raw)
                for c in y_columns:
                    if chart_type == "scatter":
                        ax.scatter(x_values, y_data[c], label=c, s=15)
                    else:
                        ax.plot(x_values, y_data[c], label=c)
                if x_values and isinstance(x_values[0], datetime):
                    fig.autofmt_xdate()
                else:
                    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

            ax.set_xlabel(x_column)
            ax.set_ylabel(", ".join(y_columns))
            ax.set_title(title or f"{x_column} vs {', '.join(y_columns)}")
            if len(y_columns) > 1:
                ax.legend()
            columns_plotted = y_columns

        fig.tight_layout()

        export_dir = cfg.get_export_dir()
        export_utils.cleanup_old_exports(export_dir)
        out_path = export_dir / export_utils.sanitize_filename(output_filename, "png")
        fig.savefig(out_path, dpi=120)
        plt.close(fig)

        logger.info("plot_csv: %s (%s) -> %s", csv_path, chart_type, out_path)

        return {
            "path": str(out_path),
            "size_kb": round(out_path.stat().st_size / 1024, 2),
            "columns_plotted": columns_plotted,
        }
