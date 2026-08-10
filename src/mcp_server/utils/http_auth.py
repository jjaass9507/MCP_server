"""Small ASGI bearer-token guard for the Streamable HTTP endpoint."""

import hmac
from collections.abc import Awaitable, Callable
from typing import Any


class BearerTokenMiddleware:
    """Require one pre-shared bearer token without ever logging it."""

    def __init__(self, app: Callable[..., Awaitable[Any]], token: str, path: str):
        self.app = app
        self._token = token
        self._path = path.rstrip("/") or "/"

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").rstrip("/") == self._path:
            headers = {
                key.lower(): value
                for key, value in scope.get("headers", [])
            }
            authorization = headers.get(b"authorization", b"").decode(
                "latin-1", "replace"
            )
            scheme, separator, supplied = authorization.partition(" ")
            valid = (
                separator == " "
                and scheme.lower() == "bearer"
                and hmac.compare_digest(supplied, self._token)
            )
            if not valid:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"www-authenticate", b"Bearer"),
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b'{"error":"unauthorized"}',
                    }
                )
                return
        await self.app(scope, receive, send)
