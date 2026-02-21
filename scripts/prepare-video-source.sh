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

normalize_usb_control_preset() {
  local raw="${1:-manual}"
  raw="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
  case "$raw" in
    manual|manual-default|manual-defaults|manual-latency)
      printf '%s\n' "manual"
      ;;
    auto|automatic|default|factory)
      printf '%s\n' "auto"
      ;;
    none|off|skip|disabled)
      printf '%s\n' "none"
      ;;
    *)
      fail "Unsupported HMDI_USB_CONTROL_PRESET '${raw}'. Supported: manual, auto, none"
      ;;
  esac
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

default_usb_controls_for_preset() {
  local preset="$1"
  case "$preset" in
    manual)
      printf '%s\n' \
        "auto_exposure=${HMDI_USB_AUTO_EXPOSURE:-1},\
exposure_time_absolute=${HMDI_USB_EXPOSURE_TIME_ABSOLUTE:-${HMDI_USB_EXPOSURE_ABSOLUTE:-157}},\
exposure_dynamic_framerate=${HMDI_USB_EXPOSURE_DYNAMIC_FRAMERATE:-0},\
white_balance_automatic=${HMDI_USB_WHITE_BALANCE_AUTOMATIC:-0},\
white_balance_temperature=${HMDI_USB_WHITE_BALANCE_TEMPERATURE:-4600},\
power_line_frequency=${HMDI_USB_POWER_LINE_FREQUENCY:-2},\
gain=${HMDI_USB_GAIN:-0}"
      ;;
    auto)
      printf '%s\n' \
        "auto_exposure=${HMDI_USB_AUTO_EXPOSURE:-3},\
exposure_dynamic_framerate=${HMDI_USB_EXPOSURE_DYNAMIC_FRAMERATE:-1},\
white_balance_automatic=${HMDI_USB_WHITE_BALANCE_AUTOMATIC:-1},\
power_line_frequency=${HMDI_USB_POWER_LINE_FREQUENCY:-2}"
      ;;
    none)
      printf '%s\n' ""
      ;;
    *)
      fail "Unsupported USB control preset: ${preset}"
      ;;
  esac
}

apply_usb_controls() {
  local video_dev="$1"
  local apply_controls="${HMDI_USB_APPLY_CONTROLS:-1}"
  local preset controls_raw controls_source
  local applied_count=0
  local failed_count=0

  if [ "$apply_controls" != "1" ]; then
    log "Skipping USB control application (HMDI_USB_APPLY_CONTROLS=${apply_controls})"
    return 0
  fi

  require_cmd v4l2-ctl

  if [ -n "${HMDI_USB_SET_CTRLS:-}" ]; then
    controls_raw="${HMDI_USB_SET_CTRLS}"
    controls_source="HMDI_USB_SET_CTRLS"
  else
    preset="$(normalize_usb_control_preset "${HMDI_USB_CONTROL_PRESET:-manual}")"
    if [ "$preset" = "none" ]; then
      log "Skipping USB control application (HMDI_USB_CONTROL_PRESET=none)"
      return 0
    fi
    controls_raw="$(default_usb_controls_for_preset "$preset")"
    controls_source="preset:${preset}"
  fi

  if [ -z "$controls_raw" ]; then
    log "No USB controls to apply"
    return 0
  fi

  IFS=',' read -r -a ctrl_pairs <<< "$controls_raw"
  for pair in "${ctrl_pairs[@]}"; do
    pair="$(printf '%s' "$pair" | tr -d '[:space:]')"
    [ -n "$pair" ] || continue
    if [[ "$pair" != *=* ]]; then
      failed_count=$((failed_count + 1))
      log "WARN: Invalid USB control entry '${pair}' (expected key=value)"
      continue
    fi
    if v4l2-ctl -d "$video_dev" --set-ctrl "$pair" >/dev/null 2>&1; then
      applied_count=$((applied_count + 1))
    else
      failed_count=$((failed_count + 1))
      log "WARN: Failed to set USB control '${pair}' on ${video_dev}"
    fi
  done

  log "Applied USB controls from ${controls_source}: ok=${applied_count} failed=${failed_count}"
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

  apply_usb_controls "$video_dev"
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
