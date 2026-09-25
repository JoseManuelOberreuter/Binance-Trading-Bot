#!/usr/bin/env bash
# One-time setup of the Oracle Cloud VM (Ubuntu 24.04). Run ON THE SERVER, after
# deploy/deploy.sh has uploaded the code to /opt/xfb:
#   bash /opt/xfb/deploy/setup_server.sh
# Installs Python deps in a venv, sets the timezone, installs the systemd services
# (enabled but NOT started — start them after migrating the bot state) and Tailscale.
set -euo pipefail

APP=/opt/xfb
TZ_NAME=America/Santiago

sudo timedatectl set-timezone "$TZ_NAME"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip

python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --upgrade pip
"$APP/.venv/bin/pip" install -r "$APP/requirements.txt"

sudo cp "$APP/deploy/xfb-bot.service" "$APP/deploy/xfb-dashboard.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable xfb-bot xfb-dashboard

# Tailscale (private network, free): official installer. `sudo tailscale up` afterwards
# prints a login URL that the owner opens to add this server to their tailnet.
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi

echo
echo "Setup done. Next:"
echo "  1) copy .env (futures testnet keys only) and live_state/ from the PC"
echo "  2) sudo systemctl start xfb-bot xfb-dashboard"
echo "  3) sudo tailscale up            # open the printed URL to log in"
echo "  4) sudo tailscale serve --bg 8501"
