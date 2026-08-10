"""Tests for generic external API request policy enforcement."""

import pytest
from mcp.server.fastmcp import FastMCP

import mcp_server.config as cfg
from mcp_server.tools import api
from mcp_server.utils.errors import ToolError


def test_request_policy_defaults_to_get_only():
    api._check_request_policy({}, "GET", "/anything", None)
    with pytest.raises(ToolError, match="POST is not allowed"):
        api._check_request_policy({}, "POST", "/anything", {"value": 1})


def test_request_policy_enforces_segment_aware_path_prefixes():
    svc = {"allowed_methods": ["GET"], "allowed_path_prefixes": ["/v1/data"]}
    api._check_request_policy(svc, "GET", "/v1/data", None)
    api._check_request_policy(svc, "GET", "/v1/data/items", None)
    with pytest.raises(ToolError, match="allowed_path_prefixes"):
        api._check_request_policy(svc, "GET", "/v1/database", None)


def test_request_policy_enforces_utf8_json_body_size():
    svc = {
        "allowed_methods": ["POST"],
        "allowed_path_prefixes": ["/v1"],
        "max_request_body_bytes": 10,
    }
    api._check_request_policy(svc, "POST", "/v1", {"x": "a"})
    with pytest.raises(ToolError, match="request body is too large"):
        api._check_request_policy(svc, "POST", "/v1", {"x": "中文中文"})


def test_api_request_schema_enumerates_supported_methods():
    mcp = FastMCP(name="test")
    api.register(mcp, cfg)
    schema = mcp._tool_manager.get_tool("api_request").parameters
    assert schema["properties"]["method"]["enum"] == [
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    ]


def test_api_policy_config_validation_rejects_invalid_values(monkeypatch):
    monkeypatch.setattr(
        cfg,
        "_api_services",
        {
            "bad": {
                "base_url": "https://example.test",
                "allowed_methods": ["TRACE"],
                "allowed_path_prefixes": ["v1"],
                "timeout_seconds": 500,
                "max_request_body_bytes": 2_000_000,
            }
        },
    )
    with pytest.raises(cfg.ConfigError) as exc_info:
        cfg.validate_config()
    message = str(exc_info.value)
    assert "allowed_methods" in message
    assert "allowed_path_prefixes" in message
    assert "timeout_seconds" in message
    assert "max_request_body_bytes" in message
