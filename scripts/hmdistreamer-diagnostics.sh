#!/usr/bin/env bash

set -u -o pipefail

LOG_LINES=80
RUN_STREAM_TEST=0

V4L_SUBDEV="${V4L_SUBDEV:-/dev/v4l-subdev2}"
VIDEO_DEV="${VIDEO_DEV:-/dev/video0}"
MEDIA_DEV="${MEDIA_DEV:-/dev/media0}"
ENV_FILE="${ENV_FILE:-/etc/hmdistreamer/hmdistreamer.env}"
RUNTIME_ENV_FILE="${RUNTIME_ENV_FILE:-/run/hmdistreamer/video.env}"
SENDER_SERVICE="${SENDER_SERVICE:-hmdistreamer-ndi-sender.service}"
BRINGUP_SERVICE="${BRINGUP_SERVICE:-hmdistreamer-hdmi-bringup.service}"

usage() {
  cat <<'EOF'
Usage: hmdistreamer-diagnostics [--logs N] [--quick-stream-test]

Options:
  --logs N             Number of journal lines to show for each service (default: 80)
  --quick-stream-test  Run a short v4l2 stream test if sender service is not active
  -h, --help           Show this help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --logs)
      shift
      [ "$#" -gt 0 ] || { echo "Missing value for --logs"; exit 2; }
      LOG_LINES="$1"
      ;;
    --quick-stream-test)
      RUN_STREAM_TEST=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1"
      usage
      exit 2
      ;;
  esac
  shift
done

section() {
  printf '\n=== %s ===\n' "$1"
}

safe_run() {
  "$@" 2>&1 || true
}

section "System"
echo "Date:     $(date -Is)"
echo "Hostname: $(hostname)"
echo "Kernel:   $(uname -srmo)"
echo "Uptime:   $(uptime -p 2>/dev/null || true)"

section "Config Files"
if [ -f "$ENV_FILE" ]; then
  echo "$ENV_FILE:"
  sed -n '1,220p' "$ENV_FILE"
else
  echo "Missing: $ENV_FILE"
fi

if [ -f "$RUNTIME_ENV_FILE" ]; then
  echo
  echo "$RUNTIME_ENV_FILE:"
  sed -n '1,120p' "$RUNTIME_ENV_FILE"
else
  echo
  echo "Missing: $RUNTIME_ENV_FILE"
fi

if [ -f /etc/hmdistreamer/ndi_sender.toml ]; then
  echo
  echo "/etc/hmdistreamer/ndi_sender.toml:"
  sed -n '1,200p' /etc/hmdistreamer/ndi_sender.toml
fi

section "Device Nodes"
for dev in "$V4L_SUBDEV" "$VIDEO_DEV" "$MEDIA_DEV"; do
  if [ -e "$dev" ]; then
    ls -l "$dev"
  else
    echo "Missing: $dev"
  fi
done

section "Service State"
echo "$SENDER_SERVICE:  $(systemctl is-active "$SENDER_SERVICE" 2>/dev/null || true) (enabled: $(systemctl is-enabled "$SENDER_SERVICE" 2>/dev/null || true))"
echo "$BRINGUP_SERVICE: $(systemctl is-active "$BRINGUP_SERVICE" 2>/dev/null || true) (enabled: $(systemctl is-enabled "$BRINGUP_SERVICE" 2>/dev/null || true))"
safe_run systemctl --no-pager --full status "$SENDER_SERVICE" | sed -n '1,80p'
safe_run systemctl --no-pager --full status "$BRINGUP_SERVICE" | sed -n '1,80p'

section "HDMI Timings"
safe_run v4l2-ctl -d "$V4L_SUBDEV" --query-dv-timings
echo
safe_run v4l2-ctl -d "$V4L_SUBDEV" --set-dv-bt-timings query

section "Video Format"
safe_run v4l2-ctl -d "$VIDEO_DEV" --get-fmt-video
echo
safe_run v4l2-ctl -d "$VIDEO_DEV" --all | sed -n '1,140p'

section "Media Graph (summary)"
safe_run media-ctl -d "$MEDIA_DEV" -p | sed -n '1,220p'

section "Process List"
safe_run ps -ef | grep -E 'hmdistreamer-ndi-sender|hmdistreamer-hdmi-bringup|ffmpeg|gst-launch|v4l2src' | grep -v grep

section "Recent Logs: $SENDER_SERVICE"
safe_run journalctl -u "$SENDER_SERVICE" -n "$LOG_LINES" --no-pager

section "Recent Logs: $BRINGUP_SERVICE"
safe_run journalctl -u "$BRINGUP_SERVICE" -n "$LOG_LINES" --no-pager

section "Health Summary"
sender_active="$(systemctl is-active "$SENDER_SERVICE" 2>/dev/null || true)"
timings="$(v4l2-ctl -d "$V4L_SUBDEV" --query-dv-timings 2>/dev/null || true)"
active_width="$(printf '%s\n' "$timings" | awk -F: '/Active width/ {gsub(/ /, "", $2); print $2; exit}')"
active_height="$(printf '%s\n' "$timings" | awk -F: '/Active height/ {gsub(/ /, "", $2); print $2; exit}')"
sender_has_fps_log=0
if journalctl -u "$SENDER_SERVICE" -n "$LOG_LINES" --no-pager 2>/dev/null | grep -q "Sending "; then
  sender_has_fps_log=1
fi

if [ "$sender_active" = "active" ]; then
  echo "[OK] Sender service is active"
else
  echo "[WARN] Sender service is not active (state: $sender_active)"
fi

if [ -n "${active_width:-}" ] && [ -n "${active_height:-}" ] && [ "$active_width" != "0" ] && [ "$active_height" != "0" ]; then
  echo "[OK] HDMI lock reports ${active_width}x${active_height}"
else
  echo "[WARN] HDMI timings did not report a valid active resolution"
fi

if [ "$sender_has_fps_log" = "1" ]; then
  echo "[OK] Sender logs include frame-rate output"
else
  echo "[WARN] Sender logs did not include 'Sending ... fps' in the last ${LOG_LINES} lines"
fi

if [ "$RUN_STREAM_TEST" = "1" ]; then
  section "Quick Stream Test"
  if [ "$sender_active" = "active" ]; then
    echo "Skipping test because $SENDER_SERVICE is active and owns $VIDEO_DEV."
    echo "Stop service first if you want this test:"
    echo "  sudo systemctl stop $SENDER_SERVICE"
  else
    safe_run v4l2-ctl -d "$VIDEO_DEV" --stream-mmap=4 --stream-count=60 --stream-to=/dev/null
  fi
fi

