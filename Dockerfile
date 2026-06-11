# ── build stage ───────────────────────────────────────────────────────────────
FROM python:3.11-slim AS build

WORKDIR /build
COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir --prefix=/install .

# ── runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Non-root user for security
RUN useradd -m -u 1000 mcp

WORKDIR /app

# Copy installed packages from build stage
COPY --from=build /install /usr/local

# /config  → mount config.toml here at runtime
# /data    → default data/database directory
RUN mkdir -p /config /data && chown mcp:mcp /config /data

USER mcp

ENV MCP_CONFIG=/config/config.toml \
    MCP_LOG_LEVEL=INFO

EXPOSE 8080

# TCP-level liveness check: verifies the port is accepting connections.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import socket; s=socket.create_connection(('localhost',8080),timeout=3); s.close()"

CMD ["mcp-server", "--transport", "sse", "--host", "0.0.0.0", "--port", "8080"]
