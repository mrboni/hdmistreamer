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
HMDI_MODE="${HMDI_MODE:-1080p-auto}"
HDMI_LOCK_RETRIES="${HDMI_LOCK_RETRIES:-15}"
HDMI_LOCK_DELAY_SEC="${HDMI_LOCK_DELAY_SEC:-2}"
DEVICE_WAIT_SEC="${DEVICE_WAIT_SEC:-120}"
VALIDATE_STREAM="${VALIDATE_STREAM:-0}"
VALIDATE_FRAME_COUNT="${VALIDATE_FRAME_COUNT:-60}"
HMDI_MEDIA_BUS_FMT="${HMDI_MEDIA_BUS_FMT:-UYVY8_1X16}"
HMDI_MEDIA_FIELD="${HMDI_MEDIA_FIELD:-none}"
HMDI_MEDIA_COLORSPACE="${HMDI_MEDIA_COLORSPACE:-srgb}"
HMDI_VIDEO_PIXFMT="${HMDI_VIDEO_PIXFMT:-UYVY}"

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
LOCKED_FPS=""
LOCKED_FPS_NUM=""
LOCKED_FPS_DEN=""

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
    1080p-auto|1080pauto)
      MODE_EDID_BASENAME="1080p60edid"
      MODE_WIDTH="1920"
      MODE_HEIGHT="1080"
      MODE_PIXELCLOCK=""
      MODE_FPS_NUM=""
      MODE_FPS_DEN=""
      ;;
    1080p60)
      MODE_EDID_BASENAME="1080p60edid"
      MODE_WIDTH="1920"
      MODE_HEIGHT="1080"
      MODE_PIXELCLOCK="148500000"
      MODE_FPS_NUM="60"
      MODE_FPS_DEN="1"
      ;;
    1080p50)
      # Keep the source-facing EDID identity stable across 50/60 profiles.
      MODE_EDID_BASENAME="1080p60edid"
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
    720p50)
      MODE_EDID_BASENAME="720p50edid"
      MODE_WIDTH="1280"
      MODE_HEIGHT="720"
      MODE_PIXELCLOCK="74250000"
      MODE_FPS_NUM="50"
      MODE_FPS_DEN="1"
      ;;
    *)
      fail "Unsupported HMDI_MODE '${HMDI_MODE}'. Supported: 720p50, 720p60, 1080p25, 1080p30, 1080p50, 1080p60, 1080p-auto"
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

parse_timing_fps() {
  local text="$1"
  printf '%s\n' "$text" | awk -F'[()]' '
    /frames per second/ {
      split($2, parts, " ")
      print parts[1]
      exit
    }
  '
}

format_expected_fps() {
  local fps_num="$1"
  local fps_den="$2"
  awk -v num="$fps_num" -v den="$fps_den" '
    BEGIN {
      if (den == 0) {
        exit 1
      }
      printf "%.2f", num / den
    }
  '
}

is_nonzero_field() {
  local value="${1:-}"
  [ -n "$value" ] && [ "$value" != "0" ]
}

derive_locked_fps_fraction() {
  local fps="$1"
  awk -v fps="$fps" '
    function abs(v) { return v < 0 ? -v : v }
    BEGIN {
      if (fps == "") {
        exit 1
      }
      f = fps + 0.0
      if (abs(f - 60.00) < 0.02) { print "60 1"; exit 0 }
      if (abs(f - 50.00) < 0.02) { print "50 1"; exit 0 }
      if (abs(f - 30.00) < 0.02) { print "30 1"; exit 0 }
      if (abs(f - 25.00) < 0.02) { print "25 1"; exit 0 }
      if (abs(f - 24.00) < 0.02) { print "24 1"; exit 0 }
      if (abs(f - 59.94) < 0.02) { print "60000 1001"; exit 0 }
      if (abs(f - 29.97) < 0.02) { print "30000 1001"; exit 0 }
      if (abs(f - 23.98) < 0.02) { print "24000 1001"; exit 0 }
      rounded = int(f + 0.5)
      if (rounded > 0) {
        print rounded " 1"
        exit 0
      }
      exit 1
    }
  '
}

wait_for_hdmi_lock() {
  local expected_width="$1"
  local expected_height="$2"
  local expected_pixelclock="$3"
  local expected_fps_num="$4"
  local expected_fps_den="$5"
  local expected_fps=""
  if [ -n "$expected_fps_num" ] || [ -n "$expected_fps_den" ]; then
    [ -n "$expected_fps_num" ] && [ -n "$expected_fps_den" ] || fail "Set both EXPECTED_FPS_NUM and EXPECTED_FPS_DEN together"
    expected_fps="$(format_expected_fps "$expected_fps_num" "$expected_fps_den")" || fail "Invalid expected FPS fraction: ${expected_fps_num}/${expected_fps_den}"
  fi
  local attempt=1
  while [ "$attempt" -le "$HDMI_LOCK_RETRIES" ]; do
    log "HDMI lock attempt ${attempt}/${HDMI_LOCK_RETRIES}"
    v4l2-ctl -d "$V4L_SUBDEV" --set-dv-bt-timings query >/dev/null 2>&1 || true

    local timings
    timings="$(v4l2-ctl -d "$V4L_SUBDEV" --query-dv-timings 2>&1 || true)"

    local width height pixelclock fps
    width="$(parse_timing_field "Active width" "$timings")"
    height="$(parse_timing_field "Active height" "$timings")"
    pixelclock="$(parse_timing_field "Pixelclock" "$timings")"
    fps="$(parse_timing_fps "$timings")"

    local width_ok=0
    local height_ok=0
    local pixelclock_ok=0
    local fps_ok=0

    if [ -n "$expected_width" ]; then
      [ "$width" = "$expected_width" ] && width_ok=1
    else
      is_nonzero_field "$width" && width_ok=1
    fi

    if [ -n "$expected_height" ]; then
      [ "$height" = "$expected_height" ] && height_ok=1
    else
      is_nonzero_field "$height" && height_ok=1
    fi

    if [ -n "$expected_pixelclock" ]; then
      [ "$pixelclock" = "$expected_pixelclock" ] && pixelclock_ok=1
    else
      is_nonzero_field "$pixelclock" && pixelclock_ok=1
    fi

    if [ -n "$expected_fps" ]; then
      [ "$fps" = "$expected_fps" ] && fps_ok=1
    else
      is_nonzero_field "$fps" && fps_ok=1
    fi

    if [ "$width_ok" = "1" ] && [ "$height_ok" = "1" ] && [ "$pixelclock_ok" = "1" ] && [ "$fps_ok" = "1" ]; then
      LOCKED_WIDTH="$width"
      LOCKED_HEIGHT="$height"
      LOCKED_PIXELCLOCK="$pixelclock"
      LOCKED_FPS="$fps"
      if fps_fraction="$(derive_locked_fps_fraction "$fps" 2>/dev/null)"; then
        LOCKED_FPS_NUM="${fps_fraction% *}"
        LOCKED_FPS_DEN="${fps_fraction#* }"
      fi
      log "HDMI locked at ${width}x${height} @ pixelclock ${pixelclock} (${fps} fps)"
      return 0
    fi

    log "Timing query not ready/mismatched (width=${width:-unknown}, expected_width=${expected_width:-auto}, height=${height:-unknown}, expected_height=${expected_height:-auto}, pixelclock=${pixelclock:-unknown}, expected_pixelclock=${expected_pixelclock:-auto}, fps=${fps:-unknown}, expected_fps=${expected_fps:-auto})"
    if [ "$attempt" -lt "$HDMI_LOCK_RETRIES" ]; then
      sleep "$HDMI_LOCK_DELAY_SEC"
    fi
    attempt=$((attempt + 1))
  done

  fail "Unable to lock HDMI (expected width=${expected_width:-auto}, height=${expected_height:-auto}, pixelclock=${expected_pixelclock:-auto}, fps=${expected_fps:-auto})"
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
  local media_fmt
  media_fmt="${HMDI_MEDIA_BUS_FMT}/${width}x${height} field:${HMDI_MEDIA_FIELD} colorspace:${HMDI_MEDIA_COLORSPACE}"
  log "Resetting media graph"
  media-ctl -d "$MEDIA_DEV" -r

  log "Enabling CSI link"
  media-ctl -d "$MEDIA_DEV" -l "'csi2':4 -> 'rp1-cfe-csi2_ch0':0 [1]"

  log "Propagating ${HMDI_MEDIA_BUS_FMT} ${width}x${height} format"
  media-ctl -d "$MEDIA_DEV" --set-v4l2 "'tc358743 11-000f':0 [fmt:${media_fmt}]"
  media-ctl -d "$MEDIA_DEV" --set-v4l2 "'csi2':0 [fmt:${media_fmt}]"
  media-ctl -d "$MEDIA_DEV" --set-v4l2 "'csi2':4 [fmt:${media_fmt}]"
}

configure_video_node() {
  local width="$1"
  local height="$2"
  log "Configuring ${VIDEO_DEV} to ${HMDI_VIDEO_PIXFMT} ${width}x${height}"
  v4l2-ctl -d "$VIDEO_DEV" -v "width=${width},height=${height},pixelformat=${HMDI_VIDEO_PIXFMT}"
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

  local selected_fps="auto"
  if [ -n "$EXPECTED_FPS_NUM" ] && [ -n "$EXPECTED_FPS_DEN" ]; then
    selected_fps="${EXPECTED_FPS_NUM}/${EXPECTED_FPS_DEN}"
  fi
  log "Selected mode: ${HMDI_MODE} (${EXPECTED_WIDTH:-auto}x${EXPECTED_HEIGHT:-auto} fps=${selected_fps})"
  log "Injecting EDID from ${EDID_PATH}"
  v4l2-ctl -d "$V4L_SUBDEV" --set-edid="file=${EDID_PATH}"
  sleep 2

  wait_for_hdmi_lock "$EXPECTED_WIDTH" "$EXPECTED_HEIGHT" "$EXPECTED_PIXELCLOCK" "$EXPECTED_FPS_NUM" "$EXPECTED_FPS_DEN"
  configure_media_graph "$LOCKED_WIDTH" "$LOCKED_HEIGHT"
  configure_video_node "$LOCKED_WIDTH" "$LOCKED_HEIGHT"
  validate_stream
  local runtime_fps_num runtime_fps_den
  runtime_fps_num="${LOCKED_FPS_NUM:-$EXPECTED_FPS_NUM}"
  runtime_fps_den="${LOCKED_FPS_DEN:-$EXPECTED_FPS_DEN}"
  if [ -z "$runtime_fps_num" ] || [ -z "$runtime_fps_den" ]; then
    runtime_fps_num="60"
    runtime_fps_den="1"
  fi
  write_runtime_env "$LOCKED_WIDTH" "$LOCKED_HEIGHT" "$LOCKED_PIXELCLOCK" "$runtime_fps_num" "$runtime_fps_den"

  log "HDMI pipeline configured successfully"
}

main "$@"
