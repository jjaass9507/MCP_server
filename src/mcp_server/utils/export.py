"""Shared helpers for writing large tool results to files under the
configured export directory (see config.get_export_dir()).

Used by database.db_query_to_file, gms.gms_history_values(to_file=True), and
tools/plotting.py — kept here (rather than in database.py) so plotting.py
doesn't need to import the database module just to reuse filename/cleanup
logic.
"""

import csv
import pathlib
import re
import time
from datetime import datetime

_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9_\-]")
_MAX_AGE_DAYS = 7


def timestamped_name(ext: str) -> str:
    """Return a timestamped filename, e.g. 'query_20260703_153000.csv'."""
    return f"query_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"


def sanitize_filename(filename: str, ext: str) -> str:
    """Return a safe '<name>.<ext>' filename derived from a caller-supplied name.

    Strips any directory components and replaces unsafe characters with '_';
    falls back to a timestamped name when filename is empty or nothing safe
    is left after sanitizing.
    """
    if not filename:
        return timestamped_name(ext)
    stem = pathlib.Path(filename).stem  # drops any directory components too
    stem = _SAFE_CHARS_RE.sub("_", stem).strip("_")
    if not stem:
        return timestamped_name(ext)
    return f"{stem}.{ext}"


def cleanup_old_exports(export_dir: pathlib.Path, max_age_days: int = _MAX_AGE_DAYS) -> None:
    """Delete *.csv / *.png files in export_dir older than max_age_days.

    Best-effort: a file that fails to delete (e.g. open elsewhere) is left
    in place and silently skipped.
    """
    cutoff = time.time() - max_age_days * 86400
    for pattern in ("*.csv", "*.png"):
        for f in export_dir.glob(pattern):
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass


def write_csv(path: pathlib.Path, columns: list[str], rows: list[dict]) -> None:
    """Write rows to path as CSV with a utf-8-sig BOM.

    utf-8-sig lets Windows Excel open the file directly with Chinese text
    intact instead of mis-detecting the encoding.
    """
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def export_csv(
    export_dir: pathlib.Path,
    columns: list[str],
    rows: list[dict],
    filename: str = "",
) -> pathlib.Path:
    """Clean up stale exports, then write rows to a new CSV in export_dir.

    Returns the path to the written file.
    """
    cleanup_old_exports(export_dir)
    path = export_dir / sanitize_filename(filename, "csv")
    write_csv(path, columns, rows)
    return path
