"""Pure-logic tests for mcp_server.tools.presentation — no Node.js required."""

from mcp_server.tools.presentation import (
    _DECK_FRAMEWORKS,
    _audit_slides,
    _build_outline,
    _word_count,
)

_TOPIC = "Topic"
_GENERAL_FULL_COUNT = len(_DECK_FRAMEWORKS["general"])
_GENERAL_P1_COUNT = sum(1 for e in _DECK_FRAMEWORKS["general"] if e[3] == 1)
_GENERAL_P1_TITLES = {
    e[1].replace("{topic}", _TOPIC) for e in _DECK_FRAMEWORKS["general"] if e[3] == 1
}


# ── _build_outline ───────────────────────────────────────────────────────

def test_build_outline_equal_to_framework_length():
    outline, note = _build_outline("Topic", _GENERAL_FULL_COUNT, "general")
    assert len(outline) == _GENERAL_FULL_COUNT
    assert note == ""


def test_build_outline_greater_than_framework_length_expands():
    n = _GENERAL_FULL_COUNT + 2
    outline, _ = _build_outline("Topic", n, "general")
    assert len(outline) == n


def test_build_outline_below_framework_trims_but_keeps_priority1():
    n = _GENERAL_FULL_COUNT - 3
    assert n > _GENERAL_P1_COUNT
    outline, note = _build_outline("Topic", n, "general")
    assert len(outline) == n
    assert note != ""
    titles = {s["title"] for s in outline}
    assert _GENERAL_P1_TITLES.issubset(titles)


def test_build_outline_below_priority1_count_returns_essential_only():
    n = _GENERAL_P1_COUNT - 1
    outline, note = _build_outline("Topic", n, "general")
    assert len(outline) == _GENERAL_P1_COUNT
    assert "essential" in note.lower() or "NOTE" in note
    titles = {s["title"] for s in outline}
    assert titles == _GENERAL_P1_TITLES


def test_build_outline_priority1_never_trimmed_at_any_size():
    for n in range(1, _GENERAL_FULL_COUNT + 5):
        outline, _ = _build_outline("Topic", n, "general")
        titles = {s["title"] for s in outline}
        assert _GENERAL_P1_TITLES.issubset(titles), f"n={n} dropped a priority-1 slide"


# ── _word_count ──────────────────────────────────────────────────────────

def test_word_count_english():
    assert _word_count("Hello World") == 2


def test_word_count_cjk_counts_each_character():
    assert _word_count("你好世界") == 4


def test_word_count_mixed_cjk_and_english():
    assert _word_count("你好 Hello 世界") == 5


def test_word_count_empty_string():
    assert _word_count("") == 0


# ── _audit_slides ────────────────────────────────────────────────────────

def test_audit_slides_flags_sparse_content_slide():
    slides = [{"layout": "content", "title": "T", "bullets": ["ok"]}]
    warnings = _audit_slides(slides)
    assert any("slide 1" in w for w in warnings)


def test_audit_slides_flags_empty_content_slide():
    slides = [{"layout": "content", "title": "T"}]
    warnings = _audit_slides(slides)
    assert any("empty" in w for w in warnings)


def test_audit_slides_no_warning_for_healthy_content_slide():
    slides = [
        {"layout": "title", "title": "Deck Title"},
        {
            "layout": "content",
            "title": "Solid slide",
            "bullets": [
                "This is a full sentence bullet with enough words",
                "Another complete bullet explaining a real point",
                "A third bullet that carries real substance here",
                "A fourth bullet rounding out the content nicely",
            ],
        },
        {"layout": "blank", "title": "S3"},
        {"layout": "blank", "title": "S4"},
        {"layout": "blank", "title": "S5"},
        {"layout": "blank", "title": "S6"},
    ]
    warnings = _audit_slides(slides)
    assert not any("slide 2" in w for w in warnings)
