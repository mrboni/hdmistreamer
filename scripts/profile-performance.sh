#!/usr/bin/env bash

set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-/etc/hmdistreamer/hmdistreamer.env}"
RUNTIME_ENV_FILE="${RUNTIME_ENV_FILE:-/run/hmdistreamer/video.env}"
NDI_CONFIG="${NDI_CONFIG:-/etc/hmdistreamer/ndi_sender.toml}"
SENDER_SERVICE="${SENDER_SERVICE:-hmdistreamer-ndi-sender.service}"

STREAM_COUNT=600
SENDER_DURATION_SEC=16
INCLUDE_FFMPEG=1
KEEP_ARTIFACTS=0
TMP_DIR=""
SENDER_WAS_ACTIVE=""

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/profile-performance.sh [options]

Options:
  --stream-count N      Frames for capture-only stage tests (default: 600)
  --duration SEC        Seconds per sender variant run (default: 16)
  --no-ffmpeg           Skip ffmpeg sender variant
  --keep-artifacts      Keep per-run logs in temp directory
  -h, --help            Show help
EOF
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1"
    exit 1
  }
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --stream-count)
      shift
      [ "$#" -gt 0 ] || { echo "Missing value for --stream-count"; exit 2; }
      STREAM_COUNT="$1"
      ;;
    --duration)
      shift
      [ "$#" -gt 0 ] || { echo "Missing value for --duration"; exit 2; }
      SENDER_DURATION_SEC="$1"
      ;;
    --no-ffmpeg)
      INCLUDE_FFMPEG=0
      ;;
    --keep-artifacts)
      KEEP_ARTIFACTS=1
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

if [ "${EUID}" -ne 0 ]; then
  echo "Run as root (sudo)."
  exit 1
fi

require_cmd v4l2-ctl
require_cmd gst-launch-1.0
require_cmd python3
require_cmd timeout

cleanup() {
  local rc=$?
  if [ "${SENDER_WAS_ACTIVE}" = "active" ]; then
    systemctl start "$SENDER_SERVICE" >/dev/null 2>&1 || true
  fi

  if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
    if [ "$KEEP_ARTIFACTS" = "1" ]; then
      echo
      echo "Kept profiling artifacts at: $TMP_DIR"
    else
      rm -rf "$TMP_DIR"
    fi
  fi
  exit "$rc"
}
trap cleanup EXIT

TMP_DIR="$(mktemp -d -t hmdistreamer-profile.XXXXXX)"
SENDER_WAS_ACTIVE="$(systemctl is-active "$SENDER_SERVICE" 2>/dev/null || true)"

echo "Stopping sender service for exclusive video access..."
systemctl stop "$SENDER_SERVICE" >/dev/null 2>&1 || true

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

echo "Running video source preparation..."
/usr/local/bin/hmdistreamer-source-prepare >"$TMP_DIR/bringup.log" 2>&1 || {
  cat "$TMP_DIR/bringup.log"
  exit 1
}

if [ -f "$RUNTIME_ENV_FILE" ]; then
  # shellcheck disable=SC1090
  source "$RUNTIME_ENV_FILE"
fi

WIDTH="${HMDI_WIDTH:-1920}"
HEIGHT="${HMDI_HEIGHT:-1080}"
FPS_NUM="${HMDI_FPS_NUM:-60}"
FPS_DEN="${HMDI_FPS_DEN:-1}"
VIDEO_DEVICE="${HMDI_VIDEO_DEVICE:-/dev/video0}"
SOURCE_GST_FORMAT="${HMDI_GST_INPUT_FORMAT:-UYVY}"

if [ "$FPS_DEN" = "0" ]; then
  echo "Invalid FPS_DEN=0"
  exit 1
fi

run_timed() {
  local name="$1"
  shift
  { time "$@"; } >"$TMP_DIR/${name}.log" 2>&1 || true
}

run_sender_variant() {
  local name="$1"
  shift
  {
    time env \
      HMDI_WIDTH="$WIDTH" \
      HMDI_HEIGHT="$HEIGHT" \
      HMDI_FPS_NUM="$FPS_NUM" \
      HMDI_FPS_DEN="$FPS_DEN" \
      HMDI_VIDEO_DEVICE="$VIDEO_DEVICE" \
      HMDI_SAMPLE_TIMEOUT_SEC=0.2 \
      HMDI_NO_FRAME_RESTART_SEC=3.0 \
      HMDI_LOG_LEVEL=INFO \
      "$@" \
      timeout "${SENDER_DURATION_SEC}s" python3 /usr/local/bin/hmdistreamer-ndi-sender --config "$NDI_CONFIG"
  } >"$TMP_DIR/sender_${name}.log" 2>&1 || true
}

echo "Running capture stage benchmarks..."
run_timed raw_v4l2 \
  v4l2-ctl -d "$VIDEO_DEVICE" --stream-mmap=4 --stream-count="$STREAM_COUNT" --stream-to=/dev/null

run_timed gst_native_fakesink \
  gst-launch-1.0 -q \
  v4l2src device="$VIDEO_DEVICE" io-mode=mmap num-buffers="$STREAM_COUNT" do-timestamp=true ! \
  "video/x-raw,format=${SOURCE_GST_FORMAT},width=${WIDTH},height=${HEIGHT},framerate=${FPS_NUM}/${FPS_DEN}" ! \
  fakesink sync=false

run_timed gst_native_to_rgbx \
  gst-launch-1.0 -q \
  v4l2src device="$VIDEO_DEVICE" io-mode=mmap num-buffers="$STREAM_COUNT" do-timestamp=true ! \
  "video/x-raw,format=${SOURCE_GST_FORMAT},width=${WIDTH},height=${HEIGHT},framerate=${FPS_NUM}/${FPS_DEN}" ! \
  videoconvert n-threads=4 ! video/x-raw,format=RGBx ! \
  fakesink sync=false

run_timed gst_native_to_uyvy \
  gst-launch-1.0 -q \
  v4l2src device="$VIDEO_DEVICE" io-mode=mmap num-buffers="$STREAM_COUNT" do-timestamp=true ! \
  "video/x-raw,format=${SOURCE_GST_FORMAT},width=${WIDTH},height=${HEIGHT},framerate=${FPS_NUM}/${FPS_DEN}" ! \
  videoconvert n-threads=4 ! video/x-raw,format=UYVY ! \
  fakesink sync=false

echo "Running sender stage benchmarks..."
sender_variants=(current_env svc_like async_rgbx_nocopy async_uyvy_nocopy)

run_sender_variant current_env

run_sender_variant svc_like \
  HMDI_CAPTURE_BACKEND=gstreamer \
  HMDI_NDI_FOURCC=UYVY \
  HMDI_GST_INPUT_FORMAT=UYVY \
  HMDI_GST_OUTPUT_FORMAT=UYVY \
  HMDI_NDI_SEND_ASYNC=0 \
  HMDI_NDI_SAFE_COPY=1 \
  HMDI_NDI_ASYNC_SAFE_COPY=1 \
  HMDI_NDI_CLOCK_VIDEO=0 \
  HMDI_APPSINK_MAX_BUFFERS=1 \
  HMDI_GST_CONVERT_THREADS=4

run_sender_variant async_rgbx_nocopy \
  HMDI_CAPTURE_BACKEND=gstreamer \
  HMDI_NDI_FOURCC=RGBX \
  HMDI_GST_INPUT_FORMAT=UYVY \
  HMDI_GST_OUTPUT_FORMAT=RGBx \
  HMDI_NDI_SEND_ASYNC=1 \
  HMDI_NDI_SAFE_COPY=0 \
  HMDI_NDI_ASYNC_SAFE_COPY=0 \
  HMDI_NDI_CLOCK_VIDEO=0 \
  HMDI_APPSINK_MAX_BUFFERS=1 \
  HMDI_GST_CONVERT_THREADS=4

run_sender_variant async_uyvy_nocopy \
  HMDI_CAPTURE_BACKEND=gstreamer \
  HMDI_NDI_FOURCC=UYVY \
  HMDI_GST_INPUT_FORMAT=UYVY \
  HMDI_GST_OUTPUT_FORMAT=UYVY \
  HMDI_NDI_SEND_ASYNC=1 \
  HMDI_NDI_SAFE_COPY=0 \
  HMDI_NDI_ASYNC_SAFE_COPY=0 \
  HMDI_NDI_CLOCK_VIDEO=0 \
  HMDI_APPSINK_MAX_BUFFERS=1 \
  HMDI_GST_CONVERT_THREADS=4

if [ "$INCLUDE_FFMPEG" = "1" ]; then
  sender_variants+=(ffmpeg_uyvy_nocopy)
  run_sender_variant ffmpeg_uyvy_nocopy \
    HMDI_CAPTURE_BACKEND=ffmpeg \
    HMDI_NDI_FOURCC=UYVY \
    HMDI_NDI_SEND_ASYNC=1 \
    HMDI_NDI_SAFE_COPY=0 \
    HMDI_NDI_ASYNC_SAFE_COPY=0 \
    HMDI_NDI_CLOCK_VIDEO=0 \
    HMDI_FFMPEG_INPUT_FORMAT=uyvy422 \
    HMDI_FFMPEG_PIX_FMT=uyvy422 \
    HMDI_FFMPEG_THREADS=4 \
    HMDI_FFMPEG_VSYNC=0
fi

echo
echo "Profiling summary:"
PROFILE_TMP_DIR="$TMP_DIR" \
PROFILE_STREAM_COUNT="$STREAM_COUNT" \
PROFILE_SOURCE_GST_FORMAT="$SOURCE_GST_FORMAT" \
PROFILE_SENDER_VARIANTS="${sender_variants[*]}" \
python3 - <<'PY'
import os
import re
import statistics
from pathlib import Path

tmp_dir = Path(os.environ["PROFILE_TMP_DIR"])
stream_count = int(os.environ["PROFILE_STREAM_COUNT"])
source_gst_format = os.environ["PROFILE_SOURCE_GST_FORMAT"]
sender_variants = os.environ["PROFILE_SENDER_VARIANTS"].split()

capture_logs = [
    ("raw_v4l2", tmp_dir / "raw_v4l2.log"),
    ("gst_native_fakesink", tmp_dir / "gst_native_fakesink.log"),
    ("gst_native_to_rgbx", tmp_dir / "gst_native_to_rgbx.log"),
    ("gst_native_to_uyvy", tmp_dir / "gst_native_to_uyvy.log"),
]

real_re = re.compile(r"^real\s+0m([0-9.]+)s$", re.M)
fps_re = re.compile(r"Sending\s+([0-9]+(?:\.[0-9]+)?)\s+fps")
age_re = re.compile(r"capture->send age ms min=[0-9.]+ avg=([0-9.]+) max=[0-9.]+")
gst_step_re = re.compile(
    r"appsink_wait avg=([0-9.]+)\s+map_copy avg=([0-9.]+)\s+ndi_send avg=([0-9.]+)\s+frame_proc avg=([0-9.]+)"
)
ff_step_re = re.compile(
    r"frame_read avg=([0-9.]+)\s+ndi_send avg=([0-9.]+)\s+frame_proc avg=([0-9.]+)"
)
stale_drop_re = re.compile(r"stale_drop=[0-9]+/[0-9]+ \(([0-9.]+)%\)")


def parse_real_seconds(text: str) -> float | None:
    m = real_re.search(text)
    if not m:
        return None
    return float(m.group(1))


def avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return statistics.mean(values)


print("Capture stage throughput:")
print(f"  source caps format={source_gst_format}")
for name, path in capture_logs:
    text = path.read_text(errors="ignore") if path.exists() else ""
    sec = parse_real_seconds(text)
    if not sec or sec <= 0:
        print(f"  {name:18s} n/a")
        continue
    fps = stream_count / sec
    print(f"  {name:18s} {fps:6.2f} fps (real {sec:5.2f}s)")

print()
print("Sender stage profiling:")
print(
    "  {name:20s} {fps:>7s} {age:>10s} {wait:>12s} {copy:>10s} {send:>10s} {read:>10s} {stale:>10s} {dominant:>14s}".format(
        name="variant",
        fps="avg_fps",
        age="age_avg",
        wait="appsink_wait",
        copy="map_copy",
        send="ndi_send",
        read="frame_read",
        stale="stale_pct",
        dominant="dominant_step",
    )
)

for name in sender_variants:
    path = tmp_dir / f"sender_{name}.log"
    text = path.read_text(errors="ignore") if path.exists() else ""
    fps_vals = [float(v) for v in fps_re.findall(text)]
    age_vals = [float(v) for v in age_re.findall(text)]
    gst_steps = [tuple(float(x) for x in m) for m in gst_step_re.findall(text)]
    ff_steps = [tuple(float(x) for x in m) for m in ff_step_re.findall(text)]
    stale_vals = [float(v) for v in stale_drop_re.findall(text)]

    avg_fps = avg(fps_vals)
    avg_age = avg(age_vals)
    avg_stale = avg(stale_vals)

    appsink_wait = map_copy = ndi_send = frame_read = 0.0
    dominant = "-"

    if gst_steps:
        appsink_wait = avg([s[0] for s in gst_steps])
        map_copy = avg([s[1] for s in gst_steps])
        ndi_send = avg([s[2] for s in gst_steps])
        dominant = max(
            [("appsink_wait", appsink_wait), ("map_copy", map_copy), ("ndi_send", ndi_send)],
            key=lambda x: x[1],
        )[0]
    elif ff_steps:
        frame_read = avg([s[0] for s in ff_steps])
        ndi_send = avg([s[1] for s in ff_steps])
        dominant = max(
            [("frame_read", frame_read), ("ndi_send", ndi_send)],
            key=lambda x: x[1],
        )[0]

    print(
        "  {name:20s} {fps:7.2f} {age:10.2f} {wait:12.2f} {copy:10.2f} {send:10.2f} {read:10.2f} {stale:10.2f} {dominant:>14s}".format(
            name=name,
            fps=avg_fps,
            age=avg_age,
            wait=appsink_wait,
            copy=map_copy,
            send=ndi_send,
            read=frame_read,
            stale=avg_stale,
            dominant=dominant,
        )
    )
PY
