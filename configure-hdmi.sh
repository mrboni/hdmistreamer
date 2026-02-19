#!/usr/bin/env bash

set -Eeuo pipefail

log() {
  printf '%s %s\n' "$(date -Is)" "$*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

require_file() {
  [ -e "$1" ] || fail "Missing required path: $1"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -n "${EDID_FILE:-}" ]; then
  EDID_PATH="$EDID_FILE"
elif [ -f "/etc/hmdistreamer/1080p60edid" ]; then
  EDID_PATH="/etc/hmdistreamer/1080p60edid"
else
  EDID_PATH="${SCRIPT_DIR}/1080p60edid"
fi

V4L_SUBDEV="${V4L_SUBDEV:-/dev/v4l-subdev2}"
VIDEO_DEV="${VIDEO_DEV:-/dev/video0}"
MEDIA_DEV="${MEDIA_DEV:-/dev/media0}"
EXPECTED_WIDTH="${EXPECTED_WIDTH:-1920}"
EXPECTED_HEIGHT="${EXPECTED_HEIGHT:-1080}"
EXPECTED_PIXELCLOCK="${EXPECTED_PIXELCLOCK:-148500000}"
HDMI_LOCK_RETRIES="${HDMI_LOCK_RETRIES:-15}"
HDMI_LOCK_DELAY_SEC="${HDMI_LOCK_DELAY_SEC:-2}"
DEVICE_WAIT_SEC="${DEVICE_WAIT_SEC:-20}"
VALIDATE_STREAM="${VALIDATE_STREAM:-0}"
VALIDATE_FRAME_COUNT="${VALIDATE_FRAME_COUNT:-60}"

parse_timing_field() {
  local field="$1"
  local text="$2"
  printf '%s\n' "$text" | awk -F: -v key="$field" '
    $0 ~ key {
      gsub(/^[ \t]+|[ \t]+$/, "", $2)
      split($2, parts, " ")
      print parts[1]
      exit
    }
  '
}

wait_for_hdmi_lock() {
  local attempt=1
  while [ "$attempt" -le "$HDMI_LOCK_RETRIES" ]; do
    log "HDMI lock attempt ${attempt}/${HDMI_LOCK_RETRIES}"
    v4l2-ctl -d "$V4L_SUBDEV" --set-dv-bt-timings query >/dev/null 2>&1 || true

    local timings
    timings="$(v4l2-ctl -d "$V4L_SUBDEV" --query-dv-timings 2>&1 || true)"

    local width height pixelclock
    width="$(parse_timing_field "Active width" "$timings")"
    height="$(parse_timing_field "Active height" "$timings")"
    pixelclock="$(parse_timing_field "Pixelclock" "$timings")"

    if [ "$width" = "$EXPECTED_WIDTH" ] && [ "$height" = "$EXPECTED_HEIGHT" ] && [ "$pixelclock" = "$EXPECTED_PIXELCLOCK" ]; then
      log "HDMI locked at ${width}x${height} @ pixelclock ${pixelclock}"
      return 0
    fi

    log "Timing query not ready/mismatched (width=${width:-unknown}, height=${height:-unknown}, pixelclock=${pixelclock:-unknown})"
    if [ "$attempt" -lt "$HDMI_LOCK_RETRIES" ]; then
      sleep "$HDMI_LOCK_DELAY_SEC"
    fi
    attempt=$((attempt + 1))
  done

  fail "Unable to lock HDMI at ${EXPECTED_WIDTH}x${EXPECTED_HEIGHT} (pixelclock ${EXPECTED_PIXELCLOCK})"
}

wait_for_path() {
  local path="$1"
  local elapsed=0
  while [ "$elapsed" -lt "$DEVICE_WAIT_SEC" ]; do
    if [ -e "$path" ]; then
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  fail "Timed out waiting for path: $path"
}

configure_media_graph() {
  log "Resetting media graph"
  media-ctl -d "$MEDIA_DEV" -r

  log "Enabling CSI link"
  media-ctl -d "$MEDIA_DEV" -l "'csi2':4 -> 'rp1-cfe-csi2_ch0':0 [1]"

  log "Propagating RGB888 1920x1080 format"
  media-ctl -d "$MEDIA_DEV" --set-v4l2 "'tc358743 11-000f':0 [fmt:RGB888_1X24/1920x1080 field:none colorspace:srgb]"
  media-ctl -d "$MEDIA_DEV" --set-v4l2 "'csi2':0 [fmt:RGB888_1X24/1920x1080 field:none colorspace:srgb]"
  media-ctl -d "$MEDIA_DEV" --set-v4l2 "'csi2':4 [fmt:RGB888_1X24/1920x1080 field:none colorspace:srgb]"
}

configure_video_node() {
  log "Configuring ${VIDEO_DEV} to RGB3 ${EXPECTED_WIDTH}x${EXPECTED_HEIGHT}"
  v4l2-ctl -d "$VIDEO_DEV" -v "width=${EXPECTED_WIDTH},height=${EXPECTED_HEIGHT},pixelformat=RGB3"
}

validate_stream() {
  if [ "$VALIDATE_STREAM" != "1" ]; then
    return 0
  fi

  log "Validating capture with ${VALIDATE_FRAME_COUNT} frames"
  v4l2-ctl -d "$VIDEO_DEV" \
    --stream-mmap=4 \
    --stream-count="$VALIDATE_FRAME_COUNT" \
    --stream-to=/dev/null >/dev/null
}

main() {
  require_cmd v4l2-ctl
  require_cmd media-ctl
  require_file "$EDID_PATH"

  wait_for_path "$V4L_SUBDEV"
  wait_for_path "$VIDEO_DEV"
  wait_for_path "$MEDIA_DEV"

  log "Injecting EDID from ${EDID_PATH}"
  v4l2-ctl -d "$V4L_SUBDEV" --set-edid="file=${EDID_PATH}"
  sleep 2

  wait_for_hdmi_lock
  configure_media_graph
  configure_video_node
  validate_stream

  log "HDMI pipeline configured successfully"
}

main "$@"
