# ── build stage ───────────────────────────────────────────────────────────────
FROM python:3.11-slim AS build

WORKDIR /build
COPY pyproject.toml .
COPY src/ src/

ARG MCP_EXTRAS=""
RUN if [ -n "$MCP_EXTRAS" ]; then \
        pip install --no-cache-dir --prefix=/install ".[${MCP_EXTRAS}]"; \
    else \
        pip install --no-cache-dir --prefix=/install .; \
    fi

# ── runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Render PPT/PPTX files to PDF and individual PNG slide images. Noto CJK keeps
# Chinese/Japanese/Korean text faithful when matching fonts are unavailable.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-impress \
        poppler-utils \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for security
RUN useradd -m -u 1000 mcp

WORKDIR /app

# Copy installed packages from build stage
COPY --from=build /install /usr/local

# /config  → mount config.toml here at runtime
# /data          → default data/database directory
# /presentations → read-only bind mount for local decks (see docker-compose.yml)
RUN mkdir -p /config /data /presentations \
    && chown mcp:mcp /config /data /presentations

USER mcp

ENV MCP_CONFIG=/config/config.toml \
    MCP_LOG_LEVEL=INFO

EXPOSE 8080

# TCP-level liveness check: verifies the port is accepting connections.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import socket; s=socket.create_connection(('localhost',8080),timeout=3); s.close()"

CMD ["mcp-server", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8080"]
