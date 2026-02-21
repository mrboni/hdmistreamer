#!/usr/bin/env bash

set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-/etc/hmdistreamer/hmdistreamer.env}"
RUNTIME_ENV_FILE="${RUNTIME_ENV_FILE:-/run/hmdistreamer/video.env}"
SENDER_SERVICE="${SENDER_SERVICE:-hmdistreamer-ndi-sender.service}"

RESTART=1
PROFILE=""
VIDEO_DEVICE="/dev/video0"
NDI_NAME=""
EXPOSURE_ABS=157
WB_TEMP=4600
GAIN=0
LINE_HZ=60

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/set-usb-profile.sh <profile> [options]

Profiles:
  microscope-latency   1280x720@30 MJPEG decode (lowest tested latency)
  microscope-detail    1600x1200@30 MJPEG decode (higher detail)

Options:
  --device /dev/videoX   USB camera device (default: /dev/video0)
  --ndi-name NAME        Override HMDI_NDI_NAME
  --exposure N           exposure_time_absolute (default: 157)
  --wb-temp N            white_balance_temperature (default: 4600)
  --gain N               gain (default: 0)
  --line-hz {0|50|60}    anti-flicker (default: 60)
  --no-restart           Do not restart sender service automatically
  -h, --help             Show help
EOF
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

normalize_profile() {
  local raw="${1:-}"
  raw="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
  case "$raw" in
    microscope-latency|usb-microscope-latency|latency)
      printf '%s\n' "microscope-latency"
      ;;
    microscope-detail|usb-microscope-detail|detail)
      printf '%s\n' "microscope-detail"
      ;;
    *)
      fail "Unsupported USB profile '${raw}'. Supported: microscope-latency, microscope-detail"
      ;;
  esac
}

line_hz_to_menu_value() {
  case "$1" in
    0|off|disabled)
      printf '%s\n' "0"
      ;;
    50|50hz)
      printf '%s\n' "1"
      ;;
    60|60hz)
      printf '%s\n' "2"
      ;;
    *)
      fail "Unsupported --line-hz value '$1'. Use 0, 50, or 60."
      ;;
  esac
}

env_quote() {
  printf '%q' "$1"
}

set_env_key() {
  local key="$1"
  local value="$2"
  local quoted
  local line
  quoted="$(env_quote "$value")"
  line="${key}=${quoted}"
  if grep -q "^${key}=" "$ENV_FILE"; then
    awk -v key="$key" -v line="$line" '
      BEGIN { re = "^" key "=" }
      $0 ~ re { print line; next }
      { print }
    ' "$ENV_FILE" >"${ENV_FILE}.tmp"
    mv "${ENV_FILE}.tmp" "$ENV_FILE"
  else
    printf '%s\n' "$line" >>"$ENV_FILE"
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --device)
      shift
      [ "$#" -gt 0 ] || fail "Missing value for --device"
      VIDEO_DEVICE="$1"
      ;;
    --ndi-name)
      shift
      [ "$#" -gt 0 ] || fail "Missing value for --ndi-name"
      NDI_NAME="$1"
      ;;
    --exposure)
      shift
      [ "$#" -gt 0 ] || fail "Missing value for --exposure"
      EXPOSURE_ABS="$1"
      ;;
    --wb-temp)
      shift
      [ "$#" -gt 0 ] || fail "Missing value for --wb-temp"
      WB_TEMP="$1"
      ;;
    --gain)
      shift
      [ "$#" -gt 0 ] || fail "Missing value for --gain"
      GAIN="$1"
      ;;
    --line-hz)
      shift
      [ "$#" -gt 0 ] || fail "Missing value for --line-hz"
      LINE_HZ="$1"
      ;;
    --no-restart)
      RESTART=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [ -z "$PROFILE" ]; then
        PROFILE="$(normalize_profile "$1")"
      else
        fail "Unexpected argument: $1"
      fi
      ;;
  esac
  shift
done

[ -n "$PROFILE" ] || {
  usage
  exit 2
}

if [ "${EUID}" -ne 0 ]; then
  fail "Run as root (sudo)."
fi

[ -e "$VIDEO_DEVICE" ] || fail "Video device not found: $VIDEO_DEVICE"

mkdir -p "$(dirname "$ENV_FILE")"
touch "$ENV_FILE"

line_menu="$(line_hz_to_menu_value "$LINE_HZ")"
controls="auto_exposure=1,exposure_time_absolute=${EXPOSURE_ABS},exposure_dynamic_framerate=0,white_balance_automatic=0,white_balance_temperature=${WB_TEMP},power_line_frequency=${line_menu},gain=${GAIN}"

case "$PROFILE" in
  microscope-latency)
    width=1280
    height=720
    drop_stale_ms=45
    pipeline="v4l2src device=${VIDEO_DEVICE} io-mode=mmap do-timestamp=true ! image/jpeg,width=1280,height=720,framerate=30/1 ! jpegdec ! videoconvert n-threads=4 ! video/x-raw,format=UYVY,width=1280,height=720,framerate=30/1"
    ;;
  microscope-detail)
    width=1600
    height=1200
    drop_stale_ms=70
    pipeline="v4l2src device=${VIDEO_DEVICE} io-mode=mmap do-timestamp=true ! image/jpeg,width=1600,height=1200,framerate=30/1 ! jpegdec ! videoconvert n-threads=4 ! video/x-raw,format=UYVY,width=1600,height=1200,framerate=30/1"
    ;;
esac

set_env_key HMDI_INPUT_KIND usb-uvc
set_env_key HMDI_VIDEO_DEVICE "$VIDEO_DEVICE"
set_env_key HMDI_CAPTURE_BACKEND gstreamer
set_env_key HMDI_NDI_FOURCC UYVY
set_env_key HMDI_GST_INPUT_FORMAT UYVY
set_env_key HMDI_GST_OUTPUT_FORMAT UYVY
set_env_key HMDI_GST_SOURCE_PIPELINE "$pipeline"
set_env_key HMDI_WIDTH "$width"
set_env_key HMDI_HEIGHT "$height"
set_env_key HMDI_FPS_NUM 30
set_env_key HMDI_FPS_DEN 1
set_env_key HMDI_APPSINK_MAX_BUFFERS 1
set_env_key HMDI_GST_USE_LEAKY_QUEUE 1
set_env_key HMDI_GST_QUEUE_MAX_BUFFERS 1
set_env_key HMDI_NDI_SEND_ASYNC 1
set_env_key HMDI_NDI_SAFE_COPY 0
set_env_key HMDI_NDI_ASYNC_SAFE_COPY 0
set_env_key HMDI_NDI_CLOCK_VIDEO 0
set_env_key HMDI_DROP_STALE_MS "$drop_stale_ms"
set_env_key HMDI_USB_APPLY_CONTROLS 1
set_env_key HMDI_USB_CONTROL_PRESET manual
set_env_key HMDI_USB_SET_CTRLS "$controls"

if [ -n "$NDI_NAME" ]; then
  set_env_key HMDI_NDI_NAME "$NDI_NAME"
fi

if [ -e "$RUNTIME_ENV_FILE" ]; then
  rm -f "$RUNTIME_ENV_FILE"
  echo "Cleared stale runtime state: $RUNTIME_ENV_FILE"
fi

echo "Applied USB profile '${PROFILE}' to ${ENV_FILE}"
echo "  device: ${VIDEO_DEVICE}"
echo "  mode:   ${width}x${height}@30"
echo "  controls: ${controls}"

if [ "$RESTART" = "1" ]; then
  systemctl restart "$SENDER_SERVICE"
  systemctl --no-pager --full status "$SENDER_SERVICE" | sed -n '1,60p'
else
  echo "No restart requested. Apply manually:"
  echo "  sudo systemctl restart ${SENDER_SERVICE}"
fi
