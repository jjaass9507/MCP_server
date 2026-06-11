#!/usr/bin/env bash
# One-shot systemd install script for the MCP server.
# Run as root or with sudo: sudo bash deploy/install-systemd.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="/opt/mcp-server"
CONFIG_DIR="/etc/mcp"
SERVICE_NAME="mcp-server"
SERVICE_USER="mcp"

echo "==> Creating service user '$SERVICE_USER' (if missing)"
id "$SERVICE_USER" &>/dev/null || useradd -r -s /sbin/nologin "$SERVICE_USER"

echo "==> Copying server files to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp -r "$REPO_DIR/src" "$REPO_DIR/pyproject.toml" "$INSTALL_DIR/"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

echo "==> Creating virtualenv and installing"
python3.11 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -q "$INSTALL_DIR"

echo "==> Installing config template (if absent)"
mkdir -p "$CONFIG_DIR"
if [[ ! -f "$CONFIG_DIR/config.toml" ]]; then
    cp "$REPO_DIR/config.toml.example" "$CONFIG_DIR/config.toml"
    echo "    IMPORTANT: Edit $CONFIG_DIR/config.toml before starting the service."
fi

echo "==> Installing systemd service"
cp "$REPO_DIR/deploy/mcp-server.service" "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

echo ""
echo "Done. Next steps:"
echo "  1. Edit $CONFIG_DIR/config.toml"
echo "  2. sudo systemctl start $SERVICE_NAME"
echo "  3. sudo systemctl status $SERVICE_NAME"
