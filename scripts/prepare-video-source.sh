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

normalize_input_kind() {
  local raw="${1:-hdmi-csi}"
  raw="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
  case "$raw" in
    hdmi|hdmi-csi|x1300|tc358743)
      printf '%s\n' "hdmi-csi"
      ;;
    usb|uvc|usb-uvc|webcam|usb-webcam)
      printf '%s\n' "usb-uvc"
      ;;
    none|skip)
      printf '%s\n' "none"
      ;;
    *)
      fail "Unsupported HMDI_INPUT_KIND '${raw}'. Supported: hdmi-csi, usb-uvc, none"
      ;;
  esac
}

write_runtime_env_from_sender_overrides() {
  local runtime_file="${RUNTIME_ENV_FILE:-/run/hmdistreamer/video.env}"
  local width="${HMDI_WIDTH:-}"
  local height="${HMDI_HEIGHT:-}"
  local fps_num="${HMDI_FPS_NUM:-}"
  local fps_den="${HMDI_FPS_DEN:-}"
  local pixelclock="${HMDI_PIXELCLOCK:-0}"
  local runtime_dir tmp_file

  if [ -z "$width" ] || [ -z "$height" ] || [ -z "$fps_num" ] || [ -z "$fps_den" ]; then
    return 0
  fi

  runtime_dir="$(dirname "$runtime_file")"
  tmp_file="${runtime_file}.tmp"
  mkdir -p "$runtime_dir"
  cat >"$tmp_file" <<EOF
HMDI_WIDTH=${width}
HMDI_HEIGHT=${height}
HMDI_FPS_NUM=${fps_num}
HMDI_FPS_DEN=${fps_den}
HMDI_PIXELCLOCK=${pixelclock}
HMDI_EDID_FILE=usb-uvc
EOF
  mv "$tmp_file" "$runtime_file"
  log "Wrote runtime sender video env to ${runtime_file}"
}

run_hdmi_prepare() {
  local prepare_cmd="${HMDI_HDMI_PREPARE_CMD:-/usr/local/bin/hmdistreamer-hdmi-bringup}"
  [ -x "$prepare_cmd" ] || fail "HDMI prepare command is not executable: ${prepare_cmd}"
  log "Preparing HDMI source via ${prepare_cmd}"
  exec "$prepare_cmd" "$@"
}

run_usb_prepare() {
  local video_dev="${HMDI_VIDEO_DEVICE:-${VIDEO_DEV:-/dev/video0}}"
  local set_format="${HMDI_USB_SET_FORMAT:-0}"
  local validate_stream="${HMDI_USB_VALIDATE_STREAM:-0}"
  local pixfmt width height frame_count

  [ -e "$video_dev" ] || fail "USB capture device not found: ${video_dev}"
  log "Preparing USB UVC source on ${video_dev}"

  if [ "$set_format" = "1" ]; then
    require_cmd v4l2-ctl
    width="${HMDI_USB_WIDTH:-${HMDI_WIDTH:-}}"
    height="${HMDI_USB_HEIGHT:-${HMDI_HEIGHT:-}}"
    pixfmt="${HMDI_USB_PIXFMT:-${HMDI_VIDEO_PIXFMT:-UYVY}}"
    [ -n "$width" ] || fail "HMDI_USB_SET_FORMAT=1 requires HMDI_USB_WIDTH or HMDI_WIDTH"
    [ -n "$height" ] || fail "HMDI_USB_SET_FORMAT=1 requires HMDI_USB_HEIGHT or HMDI_HEIGHT"
    log "Setting ${video_dev} format to ${pixfmt} ${width}x${height}"
    v4l2-ctl -d "$video_dev" -v "width=${width},height=${height},pixelformat=${pixfmt}"
  fi

  if [ "$validate_stream" = "1" ]; then
    require_cmd v4l2-ctl
    frame_count="${VALIDATE_FRAME_COUNT:-60}"
    log "Validating USB capture stream (${frame_count} frames)"
    v4l2-ctl -d "$video_dev" --stream-mmap=4 --stream-count="$frame_count" --stream-to=/dev/null >/dev/null
  fi

  write_runtime_env_from_sender_overrides
  log "USB source preparation complete"
}

main() {
  local input_kind
  input_kind="$(normalize_input_kind "${HMDI_INPUT_KIND:-hdmi-csi}")"

  case "$input_kind" in
    hdmi-csi)
      run_hdmi_prepare "$@"
      ;;
    usb-uvc)
      run_usb_prepare
      ;;
    none)
      log "Skipping source preparation (HMDI_INPUT_KIND=none)"
      ;;
  esac
}

main "$@"
