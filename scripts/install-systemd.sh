#!/usr/bin/env bash

set -Eeuo pipefail

if [ "${EUID}" -ne 0 ]; then
  echo "Run as root: sudo ./scripts/install-systemd.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "Installing configuration files..."
install -d -m 0755 /etc/hmdistreamer
install -m 0644 "${REPO_ROOT}/1080p60edid" /etc/hmdistreamer/1080p60edid

if [ ! -f /etc/hmdistreamer/hmdistreamer.env ]; then
  install -m 0644 "${REPO_ROOT}/config/hmdistreamer.env.example" /etc/hmdistreamer/hmdistreamer.env
  echo "Created /etc/hmdistreamer/hmdistreamer.env"
else
  echo "Keeping existing /etc/hmdistreamer/hmdistreamer.env"
fi

if [ ! -f /etc/hmdistreamer/ndi_sender.toml ]; then
  install -m 0644 "${REPO_ROOT}/config/ndi_sender.toml.example" /etc/hmdistreamer/ndi_sender.toml
  echo "Created /etc/hmdistreamer/ndi_sender.toml"
else
  echo "Keeping existing /etc/hmdistreamer/ndi_sender.toml"
fi

echo "Installing executable scripts..."
install -m 0755 "${REPO_ROOT}/configure-hdmi.sh" /usr/local/bin/hmdistreamer-hdmi-bringup
install -m 0755 "${REPO_ROOT}/scripts/ndi_sender.py" /usr/local/bin/hmdistreamer-ndi-sender

echo "Installing systemd units..."
install -m 0644 "${REPO_ROOT}/systemd/hmdistreamer-hdmi-bringup.service" /etc/systemd/system/hmdistreamer-hdmi-bringup.service
install -m 0644 "${REPO_ROOT}/systemd/hmdistreamer-ndi-sender.service" /etc/systemd/system/hmdistreamer-ndi-sender.service

echo "Reloading systemd and enabling services..."
systemctl daemon-reload
systemctl enable hmdistreamer-hdmi-bringup.service
systemctl enable hmdistreamer-ndi-sender.service

echo "Install complete."
echo "Next:"
echo "  1) Install dependencies listed in Docs/Deployment.md"
echo "  2) Start service: sudo systemctl start hmdistreamer-ndi-sender.service"
echo "  3) Check logs:     sudo journalctl -u hmdistreamer-ndi-sender.service -f"
