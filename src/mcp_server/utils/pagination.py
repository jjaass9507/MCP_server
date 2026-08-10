"""Shared bounded pagination helpers for inline tool responses."""

import base64
import binascii
import hashlib
import json
from collections.abc import Sequence
from typing import Any

from mcp_server.utils.errors import ToolError

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
MAX_CURSOR_OFFSET = 1_000_000


def _scope_digest(scope: str) -> str:
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]


def _encode_cursor(offset: int, scope: str) -> str:
    payload = json.dumps(
        {"v": 1, "o": offset, "s": _scope_digest(scope)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def page_window(limit: int, cursor: str, scope: str) -> tuple[int, int]:
    """Validate page inputs and return ``(offset, limit)``."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ToolError(f"limit must be an integer between 1 and {MAX_LIMIT}.")
    if not cursor:
        return 0, limit
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        offset = payload["o"]
        valid = (
            payload.get("v") == 1
            and payload.get("s") == _scope_digest(scope)
            and isinstance(offset, int)
            and not isinstance(offset, bool)
            and 0 < offset <= MAX_CURSOR_OFFSET
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
    ):
        valid = False
    if not valid:
        raise ToolError("Invalid cursor for this tool call. Start again without cursor.")
    return offset, limit


def page_from_rows(
    rows: Sequence[Any], offset: int, limit: int, scope: str
) -> dict[str, Any]:
    """Build a page from at most ``limit + 1`` already-offset rows."""
    truncated = len(rows) > limit
    # Keep native structured output while preserving the old JSON-string
    # tools' handling of datetime/Decimal and other database-specific values.
    items = json.loads(
        json.dumps(list(rows[:limit]), ensure_ascii=False, default=str)
    )
    return {
        "items": items,
        "count": len(items),
        "truncated": truncated,
        "next_cursor": _encode_cursor(offset + len(items), scope) if truncated else None,
    }


def paginate(
    items: Sequence[Any], limit: int = DEFAULT_LIMIT, cursor: str = "", *, scope: str
) -> dict[str, Any]:
    """Return a bounded page from an in-memory sequence."""
    offset, limit = page_window(limit, cursor, scope)
    return page_from_rows(items[offset : offset + limit + 1], offset, limit, scope)
