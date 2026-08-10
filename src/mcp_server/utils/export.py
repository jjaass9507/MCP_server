"""Shared helpers for writing large tool results to files under the
configured export directory (see config.get_export_dir()).

Used by database.db_query_to_file and gms.gms_history_values(to_file=True).
"""

import csv
import pathlib
import re
import secrets
import time
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9_\-]")
_MAX_AGE_DAYS = 7


def _random_suffix() -> str:
    return secrets.token_hex(3)


def timestamped_name(ext: str) -> str:
    """Return a timestamped filename with a random suffix, e.g.
    'query_20260703_153000_a3f9c1.csv'.

    The random suffix prevents two queries that land in the same second
    from getting the same filename and silently overwriting each other.
    """
    return f"query_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{_random_suffix()}.{ext}"


def sanitize_filename(filename: str, ext: str) -> str:
    """Return a safe '<name>_<random>.<ext>' filename derived from a caller-supplied name.

    Strips any directory components and replaces unsafe characters with '_',
    then appends a short random suffix so repeated calls with the same
    filename never collide and overwrite each other's data. Falls back to a
    timestamped name when filename is empty or nothing safe is left after
    sanitizing.
    """
    if not filename:
        return timestamped_name(ext)
    stem = pathlib.Path(filename).stem  # drops any directory components too
    stem = _SAFE_CHARS_RE.sub("_", stem).strip("_")
    if not stem:
        return timestamped_name(ext)
    return f"{stem}_{_random_suffix()}.{ext}"


def cleanup_old_exports(export_dir: pathlib.Path, max_age_days: int = _MAX_AGE_DAYS) -> None:
    """Delete *.csv files in export_dir older than max_age_days.

    Best-effort: a file that fails to delete (e.g. open elsewhere) is left
    in place and silently skipped.
    """
    cutoff = time.time() - max_age_days * 86400
    for f in export_dir.glob("*.csv"):
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def write_csv(
    path: pathlib.Path,
    columns: list[str],
    rows: Iterable[Mapping[str, Any]],
) -> None:
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
    rows: Iterable[Mapping[str, Any]],
    filename: str = "",
) -> pathlib.Path:
    """Clean up stale exports, then write rows to a new CSV in export_dir.

    Returns the path to the written file.
    """
    cleanup_old_exports(export_dir)
    path = export_dir / sanitize_filename(filename, "csv")
    write_csv(path, columns, rows)
    return path


def export_csv_batches(
    export_dir: pathlib.Path,
    columns: list[str],
    batches: Iterable[Iterable[Mapping[str, Any]]],
    filename: str = "",
    preview_limit: int = 5,
) -> tuple[pathlib.Path, int, list[dict[str, Any]]]:
    """Stream row batches to a CSV and retain only a small preview.

    The partially written file is removed if fetching or writing fails.
    """
    cleanup_old_exports(export_dir)
    path = export_dir / sanitize_filename(filename, "csv")
    row_count = 0
    preview: list[dict[str, Any]] = []
    try:
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for batch in batches:
                for row in batch:
                    writer.writerow(row)
                    row_count += 1
                    if len(preview) < preview_limit:
                        preview.append(dict(row))
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path, row_count, preview
