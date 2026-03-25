#!/usr/bin/env bash
# install.sh — installs hyprmon for the current user
set -euo pipefail

BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/hyprmon"
SERVICE_DIR="$HOME/.config/systemd/user"

echo "Installing hyprmon..."

mkdir -p "$BIN_DIR" "$CONFIG_DIR" "$SERVICE_DIR"

install -m 755 hyprmon.py "$BIN_DIR/hyprmon"
install -m 644 systemd/hyprmon.service "$SERVICE_DIR/hyprmon.service"

if [ ! -f "$CONFIG_DIR/config.toml" ]; then
    install -m 644 config.example.toml "$CONFIG_DIR/config.toml"
    echo "Config installed to $CONFIG_DIR/config.toml — edit it before starting the service."
else
    echo "Config already exists at $CONFIG_DIR/config.toml — not overwriting."
fi

systemctl --user daemon-reload

echo ""
echo "Done. Next steps:"
echo "  1. Edit ~/.config/hyprmon/config.toml"
echo "  2. systemctl --user enable --now hyprmon.service"
