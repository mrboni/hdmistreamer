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

V4L_SUBDEV="${V4L_SUBDEV:-/dev/v4l-subdev2}"
VIDEO_DEV="${VIDEO_DEV:-/dev/video0}"
MEDIA_DEV="${MEDIA_DEV:-/dev/media0}"
EDID_DIR="${EDID_DIR:-/etc/hmdistreamer/edid}"
RUNTIME_ENV_FILE="${RUNTIME_ENV_FILE:-/run/hmdistreamer/video.env}"
HMDI_MODE="${HMDI_MODE:-1080p60}"
HDMI_LOCK_RETRIES="${HDMI_LOCK_RETRIES:-15}"
HDMI_LOCK_DELAY_SEC="${HDMI_LOCK_DELAY_SEC:-2}"
DEVICE_WAIT_SEC="${DEVICE_WAIT_SEC:-20}"
VALIDATE_STREAM="${VALIDATE_STREAM:-0}"
VALIDATE_FRAME_COUNT="${VALIDATE_FRAME_COUNT:-60}"

MODE_EDID_BASENAME=""
MODE_WIDTH=""
MODE_HEIGHT=""
MODE_PIXELCLOCK=""
MODE_FPS_NUM=""
MODE_FPS_DEN=""
EDID_PATH=""
LOCKED_WIDTH=""
LOCKED_HEIGHT=""
LOCKED_PIXELCLOCK=""

normalize_mode() {
  local mode
  mode="$(printf '%s' "$HMDI_MODE" | tr 'A-Z' 'a-z')"
  mode="${mode%edid}"
  printf '%s' "$mode"
}

resolve_mode_defaults() {
  local mode
  mode="$(normalize_mode)"
  HMDI_MODE="$mode"
  case "$mode" in
    1080p60)
      MODE_EDID_BASENAME="1080p60edid"
      MODE_WIDTH="1920"
      MODE_HEIGHT="1080"
      MODE_PIXELCLOCK="148500000"
      MODE_FPS_NUM="60"
      MODE_FPS_DEN="1"
      ;;
    1080p50)
      MODE_EDID_BASENAME="1080p50edid"
      MODE_WIDTH="1920"
      MODE_HEIGHT="1080"
      MODE_PIXELCLOCK="148500000"
      MODE_FPS_NUM="50"
      MODE_FPS_DEN="1"
      ;;
    1080p30)
      MODE_EDID_BASENAME="1080p30edid"
      MODE_WIDTH="1920"
      MODE_HEIGHT="1080"
      MODE_PIXELCLOCK="74250000"
      MODE_FPS_NUM="30"
      MODE_FPS_DEN="1"
      ;;
    1080p25)
      MODE_EDID_BASENAME="1080p25edid"
      MODE_WIDTH="1920"
      MODE_HEIGHT="1080"
      MODE_PIXELCLOCK="74250000"
      MODE_FPS_NUM="25"
      MODE_FPS_DEN="1"
      ;;
    720p60)
      MODE_EDID_BASENAME="720p60edid"
      MODE_WIDTH="1280"
      MODE_HEIGHT="720"
      MODE_PIXELCLOCK="74250000"
      MODE_FPS_NUM="60"
      MODE_FPS_DEN="1"
      ;;
    *)
      fail "Unsupported HMDI_MODE '${HMDI_MODE}'. Supported: 720p60, 1080p25, 1080p30, 1080p50, 1080p60"
      ;;
  esac
}

resolve_edid_path() {
  if [ -n "${EDID_FILE:-}" ]; then
    EDID_PATH="$EDID_FILE"
    return 0
  fi

  local candidate
  for candidate in \
    "${EDID_DIR}/${MODE_EDID_BASENAME}" \
    "/etc/hmdistreamer/${MODE_EDID_BASENAME}" \
    "${SCRIPT_DIR}/edid/${MODE_EDID_BASENAME}" \
    "${SCRIPT_DIR}/${MODE_EDID_BASENAME}"; do
    if [ -f "$candidate" ]; then
      EDID_PATH="$candidate"
      return 0
    fi
  done

  fail "Unable to locate EDID file for mode ${HMDI_MODE}. Set EDID_FILE explicitly or install ${MODE_EDID_BASENAME} into ${EDID_DIR}."
}

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
  local expected_width="$1"
  local expected_height="$2"
  local expected_pixelclock="$3"
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

    if [ "$width" = "$expected_width" ] && [ "$height" = "$expected_height" ] && [ "$pixelclock" = "$expected_pixelclock" ]; then
      LOCKED_WIDTH="$width"
      LOCKED_HEIGHT="$height"
      LOCKED_PIXELCLOCK="$pixelclock"
      log "HDMI locked at ${width}x${height} @ pixelclock ${pixelclock}"
      return 0
    fi

    log "Timing query not ready/mismatched (width=${width:-unknown}, height=${height:-unknown}, pixelclock=${pixelclock:-unknown})"
    if [ "$attempt" -lt "$HDMI_LOCK_RETRIES" ]; then
      sleep "$HDMI_LOCK_DELAY_SEC"
    fi
    attempt=$((attempt + 1))
  done

  fail "Unable to lock HDMI at ${expected_width}x${expected_height} (pixelclock ${expected_pixelclock})"
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
  local width="$1"
  local height="$2"
  log "Resetting media graph"
  media-ctl -d "$MEDIA_DEV" -r

  log "Enabling CSI link"
  media-ctl -d "$MEDIA_DEV" -l "'csi2':4 -> 'rp1-cfe-csi2_ch0':0 [1]"

  log "Propagating RGB888 ${width}x${height} format"
  media-ctl -d "$MEDIA_DEV" --set-v4l2 "'tc358743 11-000f':0 [fmt:RGB888_1X24/${width}x${height} field:none colorspace:srgb]"
  media-ctl -d "$MEDIA_DEV" --set-v4l2 "'csi2':0 [fmt:RGB888_1X24/${width}x${height} field:none colorspace:srgb]"
  media-ctl -d "$MEDIA_DEV" --set-v4l2 "'csi2':4 [fmt:RGB888_1X24/${width}x${height} field:none colorspace:srgb]"
}

configure_video_node() {
  local width="$1"
  local height="$2"
  log "Configuring ${VIDEO_DEV} to RGB3 ${width}x${height}"
  v4l2-ctl -d "$VIDEO_DEV" -v "width=${width},height=${height},pixelformat=RGB3"
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

write_runtime_env() {
  local width="$1"
  local height="$2"
  local pixelclock="$3"
  local fps_num="$4"
  local fps_den="$5"
  local dir tmp_file
  dir="$(dirname "$RUNTIME_ENV_FILE")"
  tmp_file="${RUNTIME_ENV_FILE}.tmp"

  mkdir -p "$dir"
  cat >"$tmp_file" <<EOF
HMDI_MODE=${HMDI_MODE}
HMDI_WIDTH=${width}
HMDI_HEIGHT=${height}
HMDI_FPS_NUM=${fps_num}
HMDI_FPS_DEN=${fps_den}
HMDI_PIXELCLOCK=${pixelclock}
HMDI_EDID_FILE=${EDID_PATH}
EOF
  mv "$tmp_file" "$RUNTIME_ENV_FILE"
  log "Wrote runtime video environment to ${RUNTIME_ENV_FILE}"
}

main() {
  require_cmd v4l2-ctl
  require_cmd media-ctl

  resolve_mode_defaults
  resolve_edid_path

  EXPECTED_WIDTH="${EXPECTED_WIDTH:-$MODE_WIDTH}"
  EXPECTED_HEIGHT="${EXPECTED_HEIGHT:-$MODE_HEIGHT}"
  EXPECTED_PIXELCLOCK="${EXPECTED_PIXELCLOCK:-$MODE_PIXELCLOCK}"
  EXPECTED_FPS_NUM="${EXPECTED_FPS_NUM:-$MODE_FPS_NUM}"
  EXPECTED_FPS_DEN="${EXPECTED_FPS_DEN:-$MODE_FPS_DEN}"

  require_file "$EDID_PATH"

  wait_for_path "$V4L_SUBDEV"
  wait_for_path "$VIDEO_DEV"
  wait_for_path "$MEDIA_DEV"

  log "Selected mode: ${HMDI_MODE} (${EXPECTED_WIDTH}x${EXPECTED_HEIGHT} ${EXPECTED_FPS_NUM}/${EXPECTED_FPS_DEN})"
  log "Injecting EDID from ${EDID_PATH}"
  v4l2-ctl -d "$V4L_SUBDEV" --set-edid="file=${EDID_PATH}"
  sleep 2

  wait_for_hdmi_lock "$EXPECTED_WIDTH" "$EXPECTED_HEIGHT" "$EXPECTED_PIXELCLOCK"
  configure_media_graph "$EXPECTED_WIDTH" "$EXPECTED_HEIGHT"
  configure_video_node "$EXPECTED_WIDTH" "$EXPECTED_HEIGHT"
  validate_stream
  write_runtime_env "$EXPECTED_WIDTH" "$EXPECTED_HEIGHT" "$LOCKED_PIXELCLOCK" "$EXPECTED_FPS_NUM" "$EXPECTED_FPS_DEN"

  log "HDMI pipeline configured successfully"
}

main "$@"
