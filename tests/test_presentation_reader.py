"""Pure-logic tests for presentation reader paging and cache identity."""

import os

import pytest

from mcp_server.tools.presentation_reader import (
    MAX_SLIDES_PER_CALL,
    _cache_key,
    _slide_window,
)
from mcp_server.utils.errors import ToolError


def test_slide_window_returns_next_slide():
    assert _slide_window(total=10, start_slide=1, limit=3) == (0, 3, 4)


def test_slide_window_final_page_has_no_cursor():
    assert _slide_window(total=10, start_slide=9, limit=3) == (8, 10, None)


def test_slide_window_caps_requested_limit():
    start, end, next_slide = _slide_window(total=20, start_slide=1, limit=100)
    assert (start, end, next_slide) == (0, MAX_SLIDES_PER_CALL, MAX_SLIDES_PER_CALL + 1)


@pytest.mark.parametrize(
    ("total", "start_slide", "limit"),
    [(0, 1, 1), (3, 0, 1), (3, 4, 1), (3, 1, 0)],
)
def test_slide_window_rejects_invalid_ranges(total, start_slide, limit):
    with pytest.raises(ToolError):
        _slide_window(total, start_slide, limit)


def test_cache_key_changes_when_file_changes(tmp_path):
    source = tmp_path / "deck.pptx"
    source.write_bytes(b"first")
    first_key = _cache_key(source)

    source.write_bytes(b"second version")
    os.utime(source, None)

    assert _cache_key(source) != first_key
