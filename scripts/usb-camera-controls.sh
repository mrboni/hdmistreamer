#!/usr/bin/env bash

set -Eeuo pipefail

VIDEO_DEV="${HMDI_VIDEO_DEVICE:-${VIDEO_DEV:-/dev/video0}}"

COMMON_CONTROLS=(
  auto_exposure
  exposure_time_absolute
  exposure_dynamic_framerate
  white_balance_automatic
  white_balance_temperature
  gain
  power_line_frequency
  brightness
  contrast
  saturation
  sharpness
  gamma
  backlight_compensation
)

usage() {
  cat <<'EOF'
Usage: hmdistreamer-usb-controls [--device /dev/videoX] <command> [args]

Commands:
  list
      Show camera controls and menu choices.

  all
      Dump full device state (`v4l2-ctl --all`).

  get [CONTROL ...]
      Show control values. If no controls are provided, prints a common set.

  set CONTROL=VALUE [CONTROL=VALUE ...]
      Set one or more controls.
      Example:
        hmdistreamer-usb-controls set auto_exposure=1 exposure_time_absolute=140 gain=5

  preset <manual|auto>
      Apply a preset:
      - manual: manual exposure + manual white balance + dynamic framerate off
      - auto: automatic exposure + automatic white balance
EOF
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

normalize_preset() {
  local raw="${1:-}"
  raw="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
  case "$raw" in
    manual|manual-default|manual-defaults|manual-latency)
      printf '%s\n' "manual"
      ;;
    auto|automatic|default|factory)
      printf '%s\n' "auto"
      ;;
    *)
      fail "Unsupported preset '${raw}'. Supported: manual, auto"
      ;;
  esac
}

apply_ctrl_spec() {
  local spec="$1"
  local pair key value
  local ok_count=0
  local fail_count=0

  IFS=',' read -r -a pairs <<< "$spec"
  for pair in "${pairs[@]}"; do
    pair="$(printf '%s' "$pair" | tr -d '[:space:]')"
    [ -n "$pair" ] || continue
    if [[ "$pair" != *=* ]]; then
      printf 'WARN: skipping invalid control entry: %s\n' "$pair" >&2
      fail_count=$((fail_count + 1))
      continue
    fi
    key="${pair%%=*}"
    value="${pair#*=}"
    if v4l2-ctl -d "$VIDEO_DEV" --set-ctrl "${key}=${value}" >/dev/null 2>&1; then
      ok_count=$((ok_count + 1))
    else
      printf 'WARN: failed to set %s=%s on %s\n' "$key" "$value" "$VIDEO_DEV" >&2
      fail_count=$((fail_count + 1))
    fi
  done

  printf 'Applied controls on %s: ok=%d failed=%d\n' "$VIDEO_DEV" "$ok_count" "$fail_count"
}

preset_ctrl_spec() {
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
    *)
      fail "Unsupported preset '${preset}'"
      ;;
  esac
}

join_ctrl_names() {
  local out=""
  local name
  for name in "$@"; do
    if [ -z "$out" ]; then
      out="$name"
    else
      out="${out},${name}"
    fi
  done
  printf '%s\n' "$out"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -d|--device)
      shift
      [ "$#" -gt 0 ] || fail "Missing value for --device"
      VIDEO_DEV="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      break
      ;;
  esac
  shift
done

[ -e "$VIDEO_DEV" ] || fail "Video device not found: $VIDEO_DEV"
require_cmd v4l2-ctl

cmd="${1:-}"
[ -n "$cmd" ] || {
  usage
  exit 2
}
shift

case "$cmd" in
  list)
    v4l2-ctl -d "$VIDEO_DEV" --list-ctrls-menus
    ;;
  all)
    v4l2-ctl -d "$VIDEO_DEV" --all
    ;;
  get)
    if [ "$#" -eq 0 ]; then
      ctrl_list="$(join_ctrl_names "${COMMON_CONTROLS[@]}")"
    else
      ctrl_list="$(join_ctrl_names "$@")"
    fi
    v4l2-ctl -d "$VIDEO_DEV" --get-ctrl="$ctrl_list"
    ;;
  set)
    [ "$#" -gt 0 ] || fail "set requires at least one CONTROL=VALUE entry"
    ctrl_spec="$(join_ctrl_names "$@")"
    apply_ctrl_spec "$ctrl_spec"
    ;;
  preset)
    [ "$#" -gt 0 ] || fail "preset requires a name (manual|auto)"
    preset="$(normalize_preset "$1")"
    ctrl_spec="$(preset_ctrl_spec "$preset")"
    apply_ctrl_spec "$ctrl_spec"
    v4l2-ctl -d "$VIDEO_DEV" --get-ctrl="$(join_ctrl_names "${COMMON_CONTROLS[@]}")"
    ;;
  *)
    fail "Unknown command '${cmd}'. Use --help."
    ;;
esac
