#!/usr/bin/env bash

set -Eeuo pipefail

if [ "${EUID}" -ne 0 ]; then
  echo "Run as root: sudo ./scripts/install-deps.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "Installing apt dependencies..."
apt update
apt install -y \
  v4l-utils \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  python3 \
  python3-pip \
  python3-numpy \
  python3-gi \
  python3-gst-1.0 \
  build-essential \
  cmake

echo "Installing Python dependencies..."
python3 -m pip install --break-system-packages -r "${REPO_ROOT}/requirements.txt"

echo "Dependency installation complete."
