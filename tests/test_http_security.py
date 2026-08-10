"""Tests for Streamable HTTP transport security defaults and bearer auth."""

import asyncio

import pytest

import mcp_server.config as cfg
from mcp_server.server import create_server
from mcp_server.utils.http_auth import BearerTokenMiddleware


async def _call_asgi(app, *, path="/mcp", authorization=""):
    sent = []
    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode("ascii")))
    scope = {"type": "http", "path": path, "headers": headers}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


def test_bearer_middleware_rejects_missing_or_wrong_token():
    async def inner(scope, receive, send):
        raise AssertionError("unauthorized request reached inner app")

    app = BearerTokenMiddleware(inner, "correct-token", "/mcp")
    for authorization in ("", "Basic abc", "Bearer wrong-token"):
        sent = asyncio.run(_call_asgi(app, authorization=authorization))
        assert sent[0]["status"] == 401
        assert (b"www-authenticate", b"Bearer") in sent[0]["headers"]


def test_bearer_middleware_accepts_valid_token_and_normalizes_trailing_slash():
    reached = False

    async def inner(scope, receive, send):
        nonlocal reached
        reached = True
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = BearerTokenMiddleware(inner, "correct-token", "/mcp")
    sent = asyncio.run(
        _call_asgi(app, path="/mcp/", authorization="Bearer correct-token")
    )
    assert reached is True
    assert sent[0]["status"] == 204


def test_nonlocal_streamable_http_requires_token(monkeypatch):
    monkeypatch.setattr(
        cfg,
        "get_http_config",
        lambda: {"bearer_token": "", "bearer_token_env": "MCP_HTTP_BEARER_TOKEN"},
    )
    with pytest.raises(cfg.ConfigError, match="requires bearer authentication"):
        cfg.validate_http_runtime("0.0.0.0", 8080, transport="streamable-http")


def test_nonlocal_streamable_http_accepts_configured_token(monkeypatch):
    monkeypatch.setattr(
        cfg,
        "get_http_config",
        lambda: {
            "bearer_token": "a" * 32,
            "bearer_token_env": "MCP_HTTP_BEARER_TOKEN",
        },
    )
    cfg.validate_http_runtime("0.0.0.0", 8080, transport="streamable-http")


def test_short_bearer_token_is_rejected_even_on_localhost(monkeypatch):
    monkeypatch.setattr(
        cfg,
        "get_http_config",
        lambda: {"bearer_token": "too-short", "bearer_token_env": "TOKEN"},
    )
    with pytest.raises(cfg.ConfigError, match="at least 32 characters"):
        cfg.validate_http_runtime("127.0.0.1", 8080, transport="streamable-http")


def test_sse_is_local_only(monkeypatch):
    monkeypatch.setattr(cfg, "get_http_config", lambda: {"bearer_token": "token"})
    with pytest.raises(cfg.ConfigError, match="restricted to localhost"):
        cfg.validate_http_runtime("0.0.0.0", 8080, transport="sse")


def test_cli_port_override_is_validated(monkeypatch):
    monkeypatch.setattr(cfg, "get_http_config", lambda: {"bearer_token": ""})
    with pytest.raises(cfg.ConfigError, match="HTTP port"):
        cfg.validate_http_runtime("127.0.0.1", 70000, transport="streamable-http")


def test_server_enables_host_and_origin_validation(monkeypatch):
    monkeypatch.setattr(
        cfg,
        "get_http_config",
        lambda: {
            "host": "127.0.0.1",
            "port": 8080,
            "allowed_hosts": ["localhost:*"],
            "allowed_origins": ["http://localhost:*"],
            "bearer_token_env": "MCP_HTTP_BEARER_TOKEN",
            "bearer_token": "",
        },
    )
    server = create_server()

    security = server.settings.transport_security
    assert server.settings.host == "127.0.0.1"
    assert server.settings.streamable_http_path == "/mcp"
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["localhost:*"]
    assert security.allowed_origins == ["http://localhost:*"]
