"""External HTTP API tools.

A config-driven scaffold for calling external REST APIs. Services are defined
under [api.services] in config.toml by name. The model only ever sees the
service NAME (e.g. 'weather') — the api_key and base_url are injected here on
the server side, mirroring how database.py hides connection strings behind a
db_name alias.

HOW TO ADD A NEW API
====================
1. Add a service block to config.toml under [api.services.<name>] with at
   least a base_url (plus api_key / auth_header / auth_prefix if it needs a key).
2. That service is immediately usable via the generic api_request() tool.
3. (Optional) For a friendlier, typed wrapper, add a dedicated @mcp.tool()
   below following the same pattern — give it a clear docstring describing the
   endpoint so the model knows how to call it.
"""

from typing import TYPE_CHECKING, Any

import base64
import mimetypes
import pathlib

import httpx

from mcp.server.fastmcp import FastMCP

from mcp_server.utils.errors import ToolError
from mcp_server.utils.logging import get_logger

if TYPE_CHECKING:
    import mcp_server.config as _CfgModule

logger = get_logger("api")

# Cap response bodies so a huge payload can't blow up the context window.
_MAX_BODY_BYTES = 100_000

# Reject oversized images early — a multi-MB base64 blob bloats the request and
# is usually rejected by the push backend anyway.
_MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _response_payload(resp: httpx.Response) -> dict:
    """Build the {"status", "body"} result from an httpx response.

    Parses JSON when possible, falls back to text, and truncates oversized bodies.
    """
    raw = resp.content[:_MAX_BODY_BYTES]
    truncated = len(resp.content) > _MAX_BODY_BYTES
    try:
        body: Any = resp.json() if not truncated else raw.decode("utf-8", "replace")
    except ValueError:
        body = raw.decode("utf-8", "replace")
    if truncated and isinstance(body, str):
        body = {"_truncated": True, "text": body}
    return {"status": resp.status_code, "body": body}


def _image_to_img_tag(image_path: str, cfg: "_CfgModule") -> str:
    """Read an image file and return an inline base64 <img> tag.

    Encoding happens here on the server so a huge base64 string never has to
    pass through the model's output. The path is access-checked against
    allowed_paths via cfg.check_path.
    """
    p = pathlib.Path(image_path).resolve()
    cfg.check_path(p)  # raises ToolError if outside allowed_paths
    if not p.is_file():
        raise ToolError(f"Image file not found: {p}")
    data = p.read_bytes()
    if len(data) > _MAX_IMAGE_BYTES:
        raise ToolError(
            f"Image is too large ({len(data)} bytes, limit {_MAX_IMAGE_BYTES}). "
            f"Export a smaller chart (e.g. lower DPI) and try again."
        )
    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    b64 = base64.b64encode(data).decode("ascii")
    return f"<img src='data:{mime};base64,{b64}'>"


def register(mcp: FastMCP, cfg: "_CfgModule") -> None:

    def _resolve_service_name(service: str) -> str:
        if service:
            return service
        names = cfg.list_api_names()
        if not names:
            raise ToolError("No API services configured.")
        if len(names) == 1:
            return names[0]
        raise ToolError(f"Please specify service. Available: {', '.join(names)}")

    @mcp.tool()
    def api_list_services() -> str:
        """List the names of all API services configured in config.toml.

        Use one of the returned names as the service parameter in api_request().
        """
        names = cfg.list_api_names()
        if not names:
            raise ToolError(
                "No API services are configured. "
                "Add entries under [api.services] in config.toml."
            )
        return f"Available API services: {', '.join(names)}. Use one of these as the service parameter."

    @mcp.tool()
    def api_request(
        service: str = "",
        method: str = "GET",
        path: str = "",
        query: dict = {},
        json_body: Any = None,
    ) -> dict:
        """Make an HTTP request to a named API service.

        The service's base_url and authentication header are injected from
        config.toml — you never pass an API key here. Call api_list_services()
        to see available service names.

        Returns {"status": int, "body": <parsed JSON or text>}.

        Args:
            service:   API service name from config.toml. Auto-selected if only one is configured.
            method:    HTTP method (GET, POST, PUT, PATCH, DELETE). Default: GET.
            path:      Path appended to the service base_url (e.g. '/weather').
            query:     Optional query-string parameters as a dict.
            json_body: Optional JSON request body (for POST/PUT/PATCH).
        """
        svc = cfg.resolve_api(_resolve_service_name(service))

        headers = dict(svc.get("headers", {}))
        if svc.get("api_key"):
            auth_header = svc.get("auth_header", "Authorization")
            auth_prefix = svc.get("auth_prefix", "Bearer ")
            headers[auth_header] = auth_prefix + svc["api_key"]

        url = svc["base_url"].rstrip("/") + "/" + path.lstrip("/")

        try:
            with httpx.Client(timeout=30, verify=svc.get("verify", True)) as client:
                resp = client.request(
                    method.upper(),
                    url,
                    params=query or None,
                    json=json_body,
                    headers=headers,
                )
        except httpx.HTTPError as e:
            raise ToolError(f"API request failed: {e}") from e

        # Never log the api_key or headers — only the request shape and status.
        logger.info(
            "api_request: service=%s %s %s -> %s",
            service, method.upper(), path, resp.status_code,
        )

        return _response_payload(resp)

    # ---------------------------------------------------------------
    # Add per-API convenience wrappers below, following the pattern above.
    # ---------------------------------------------------------------

    @mcp.tool()
    def push_notify(service: str = "", title: str = "", content: str = "",
                    image_path: str = "", push_to_list: list = []) -> dict:
        """Send a push notification via a configured Push+ service (email / group).

        Fills the Push+ template's $_title and $_content variables. `content` may
        contain simple inline HTML so key facts render nicely — e.g.
        content="<b>Order</b>: 12345<br><b>Status</b>: shipped".

        To attach an image (e.g. a chart), pass image_path — a path to an image
        FILE on the server (must be inside allowed_paths in config.toml). The
        server reads the file and embeds it as an inline base64 <img> appended to
        the content. Do NOT paste base64 into content yourself: a base64 blob is
        huge and will blow past the model's output token limit. Hand over a file
        path and let the server do the encoding.

        Recipients default to the Push+ template's configured list — pass push_to_list
        (e.g. ["K12345","K22345"]) only to override them.

        Returns {"status": int, "body": <parsed JSON or text>}.

        Args:
            service:      Push+ service name from config.toml. Auto-selected if only one is configured.
            title:        Subject / heading; fills the template's $_title variable.
            content:      Body text; may include simple inline HTML. Fills the $_content variable.
            image_path:   Optional path to an image file to embed (JPG renders most reliably in email/Notes).
            push_to_list: Optional list of recipient IDs that REPLACES the template's recipients.
        """
        svc = cfg.resolve_api(_resolve_service_name(service))
        token = svc.get("token") or svc.get("api_key")
        if not token:
            raise ToolError(
                f"Push service '{service or 'default'}' has no token. "
                f'Add token = "..." under its [api.services.*] block in config.toml.'
            )

        body_html = content
        if image_path:
            body_html += _image_to_img_tag(image_path, cfg)

        payload: dict[str, Any] = {
            "token": token,
            "push_para": {"title": title, "content": body_html},
        }
        if push_to_list:
            payload["push_to_list"] = push_to_list

        try:
            with httpx.Client(timeout=30, verify=svc.get("verify", True)) as client:
                resp = client.post(svc["base_url"], json=payload)
        except httpx.HTTPError as e:
            raise ToolError(f"Push notification failed: {e}") from e

        # Never log the token or message contents — only service + status.
        logger.info("push_notify: service=%s -> %s", service or "default", resp.status_code)

        return _response_payload(resp)
