#!/usr/bin/env bash

set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-/etc/hmdistreamer/hmdistreamer.env}"
RESTART=1
KEEP_OVERRIDES=0
MODE=""

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/set-mode.sh <mode> [--no-restart] [--keep-overrides]

Supported modes:
  720p60
  1080p25
  1080p30
  1080p50
  1080p60

By default this removes explicit EDID/timing/sender dimension overrides from
/etc/hmdistreamer/hmdistreamer.env so mode profiles can take effect.
EOF
}

is_supported_mode() {
  case "$1" in
    720p60|1080p25|1080p30|1080p50|1080p60)
      return 0
      ;;
  esac
  return 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-restart)
      RESTART=0
      ;;
    --keep-overrides)
      KEEP_OVERRIDES=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [ -z "$MODE" ]; then
        MODE="$(printf '%s' "$1" | tr 'A-Z' 'a-z')"
      else
        echo "Unexpected argument: $1"
        usage
        exit 2
      fi
      ;;
  esac
  shift
done

if [ -z "$MODE" ]; then
  usage
  exit 2
fi

if ! is_supported_mode "$MODE"; then
  echo "Unsupported mode: $MODE"
  usage
  exit 2
fi

if [ "${EUID}" -ne 0 ]; then
  echo "Run as root (sudo)."
  exit 1
fi

mkdir -p "$(dirname "$ENV_FILE")"
touch "$ENV_FILE"

if grep -q '^HMDI_MODE=' "$ENV_FILE"; then
  sed -i "s/^HMDI_MODE=.*/HMDI_MODE=${MODE}/" "$ENV_FILE"
else
  echo "HMDI_MODE=${MODE}" >> "$ENV_FILE"
fi

echo "Set HMDI_MODE=${MODE} in ${ENV_FILE}"

if [ "$KEEP_OVERRIDES" = "0" ]; then
  sed -i \
    -e '/^EDID_FILE=/d' \
    -e '/^EXPECTED_WIDTH=/d' \
    -e '/^EXPECTED_HEIGHT=/d' \
    -e '/^EXPECTED_PIXELCLOCK=/d' \
    -e '/^EXPECTED_FPS_NUM=/d' \
    -e '/^EXPECTED_FPS_DEN=/d' \
    -e '/^HMDI_WIDTH=/d' \
    -e '/^HMDI_HEIGHT=/d' \
    -e '/^HMDI_FPS_NUM=/d' \
    -e '/^HMDI_FPS_DEN=/d' \
    "$ENV_FILE"
  echo "Removed explicit EDID/timing/sender-dimension overrides from ${ENV_FILE}."
elif grep -Eq '^(EDID_FILE|EXPECTED_WIDTH|EXPECTED_HEIGHT|EXPECTED_PIXELCLOCK|EXPECTED_FPS_NUM|EXPECTED_FPS_DEN|HMDI_WIDTH|HMDI_HEIGHT|HMDI_FPS_NUM|HMDI_FPS_DEN)=' "$ENV_FILE"; then
  echo "Warning: ${ENV_FILE} contains explicit overrides that can supersede profile defaults."
  echo "Review these keys if the mode does not take effect:"
  grep -E '^(EDID_FILE|EXPECTED_WIDTH|EXPECTED_HEIGHT|EXPECTED_PIXELCLOCK|EXPECTED_FPS_NUM|EXPECTED_FPS_DEN|HMDI_WIDTH|HMDI_HEIGHT|HMDI_FPS_NUM|HMDI_FPS_DEN)=' "$ENV_FILE" || true
fi

if [ "$RESTART" = "1" ]; then
  systemctl restart hmdistreamer-ndi-sender.service
  systemctl --no-pager --full status hmdistreamer-ndi-sender.service | sed -n '1,60p'
else
  echo "No restart requested. Apply manually:"
  echo "  sudo systemctl restart hmdistreamer-ndi-sender.service"
fi
