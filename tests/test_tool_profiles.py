"""Tests for category-based tool registration profiles."""

import pytest

import mcp_server.config as cfg
from mcp_server.server import _server_instructions, create_server


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("all", cfg.TOOL_CATEGORIES),
        ("core", frozenset({"filesystem", "database", "custom", "api"})),
        ("gms", frozenset({"custom", "gms"})),
        ("presentation", frozenset({"custom", "presentation"})),
        ("minimal", frozenset({"custom"})),
    ],
)
def test_profile_resolves_expected_categories(profile, expected, monkeypatch):
    monkeypatch.setattr(cfg, "_tool_profile", profile)
    monkeypatch.setattr(cfg, "_enabled_categories", None)
    assert cfg.get_enabled_tool_categories() == expected


def test_enabled_categories_overrides_profile(monkeypatch):
    monkeypatch.setattr(cfg, "_tool_profile", "all")
    monkeypatch.setattr(cfg, "_enabled_categories", ["database", "gms"])
    assert cfg.get_enabled_tool_categories() == frozenset({"database", "gms"})


def test_gms_profile_registers_only_its_categories(monkeypatch):
    monkeypatch.setattr(
        cfg, "get_enabled_tool_categories", lambda: frozenset({"custom", "gms"})
    )
    mcp = create_server()

    assert mcp._tool_manager.get_tool("system_info") is not None
    assert mcp._tool_manager.get_tool("gms_list_equipment") is not None
    assert mcp._tool_manager.get_tool("db_query") is None
    assert mcp._tool_manager.get_tool("read_file") is None
    assert mcp._tool_manager.get_tool("create_presentation") is None


def test_core_profile_excludes_domain_specific_categories(monkeypatch):
    categories = frozenset({"filesystem", "database", "custom", "api"})
    monkeypatch.setattr(cfg, "get_enabled_tool_categories", lambda: categories)
    mcp = create_server()

    assert mcp._tool_manager.get_tool("read_file") is not None
    assert mcp._tool_manager.get_tool("db_query") is not None
    assert mcp._tool_manager.get_tool("api_request") is not None
    assert mcp._tool_manager.get_tool("gms_list_equipment") is None
    assert mcp._tool_manager.get_tool("create_presentation") is None


def test_instructions_only_reference_enabled_categories():
    instructions = _server_instructions(frozenset({"custom", "gms"}))
    assert "gms_history_aggregate" in instructions
    assert "db_list_databases" not in instructions
    assert "api_list_services" not in instructions
    assert "list_presentation_styles" not in instructions


def test_invalid_profile_is_rejected(monkeypatch):
    monkeypatch.setattr(cfg, "_tools_cfg", {"profile": "unknown"})
    monkeypatch.setattr(cfg, "_tool_profile", "unknown")
    monkeypatch.setattr(cfg, "_enabled_categories", None)
    with pytest.raises(cfg.ConfigError, match="tools.profile"):
        cfg.validate_config()


def test_unknown_enabled_category_is_rejected(monkeypatch):
    monkeypatch.setattr(cfg, "_tools_cfg", {})
    monkeypatch.setattr(cfg, "_tool_profile", "all")
    monkeypatch.setattr(cfg, "_enabled_categories", ["database", "unknown"])
    with pytest.raises(cfg.ConfigError, match="unknown categories: unknown"):
        cfg.validate_config()
