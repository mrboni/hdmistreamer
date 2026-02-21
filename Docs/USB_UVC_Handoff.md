# USB UVC -> NDI Handoff

## Baseline

- Stable camera baseline tag: `stable-camera`
- Baseline commit reference: `c40a0d8` (tagged)
- Current proven path: X1300 HDMI capture at native `UYVY -> NDI UYVY` with low latency and reboot persistence.

## Goal

Add USB webcam ingest while preserving the same low-latency/high-throughput design principles:

- prefer native colorspace path (avoid conversion)
- keep queues shallow and leaky only where needed
- instrument and compare stage latency, not just FPS

## Current Architecture (Reusable)

- Source prep entrypoint: `/usr/local/bin/hmdistreamer-source-prepare`
  - `HMDI_INPUT_KIND=hdmi-csi` delegates to HDMI bring-up
  - `HMDI_INPUT_KIND=usb-uvc` prepares/validates USB capture device
- Sender: `/usr/local/bin/hmdistreamer-ndi-sender`
  - `capture_backend=gstreamer` (default, preferred)
  - latency telemetry in logs (`capture->send age`, step timings)
  - optional `HMDI_GST_SOURCE_PIPELINE` for custom webcam graphs (MJPEG decode, etc)
- Profiling helper: `/usr/local/bin/hmdistreamer-profile-performance`

## Recommended Development Sequence

1. Enumerate USB device capabilities.
   - `v4l2-ctl --list-devices`
   - `v4l2-ctl -d /dev/videoX --list-formats-ext`
2. Attempt native UYVY/YUYV path first.
   - Prefer format directly supported by webcam and mappable to NDI fourcc with minimal conversion.
3. If webcam outputs MJPEG/H264 only, use `HMDI_GST_SOURCE_PIPELINE`.
   - Decode as early as needed, then convert once to target NDI input format.
4. Keep sender output aligned with NDI fourcc.
   - For example `HMDI_NDI_FOURCC=UYVY` with `HMDI_GST_OUTPUT_FORMAT=UYVY`.
5. Profile each variant.
   - Use `hmdistreamer-profile-performance`.
   - Compare `appsink_wait`, `map_copy`, `ndi_send`, `capture->send age`, and stale-drop %.

## USB Config Baseline (Starting Point)

Set in `/etc/hmdistreamer/hmdistreamer.env`:

```bash
HMDI_INPUT_KIND=usb-uvc
HMDI_VIDEO_DEVICE=/dev/videoX
HMDI_WIDTH=1920
HMDI_HEIGHT=1080
HMDI_FPS_NUM=60
HMDI_FPS_DEN=1
HMDI_NDI_FOURCC=UYVY
HMDI_GST_INPUT_FORMAT=UYVY
HMDI_GST_OUTPUT_FORMAT=UYVY
```

Optional when needed:

```bash
HMDI_GST_SOURCE_PIPELINE=v4l2src device=/dev/videoX io-mode=mmap do-timestamp=true ! image/jpeg,width=1920,height=1080,framerate=60/1 ! jpegdec ! videoconvert ! video/x-raw,format=UYVY,width=1920,height=1080,framerate=60/1
```

## Performance Guardrails

For 1080p60 class USB sources on this hardware:

- Throughput target: near source FPS (or clearly documented device limit)
- Latency target: `capture->send age` roughly bounded near one frame period under steady state
- Stability target: no service restarts, no persistent stale-drop growth

## Acceptance Checklist

- Service starts automatically after reboot with USB config.
- Stable NDI discovery and image continuity for 30+ minutes.
- Profiling report captured and committed for chosen USB pipeline.
- Any residual limitations documented with exact camera model and mode.

## Notes

- Keep HDMI/X1300 baseline behavior intact while adding USB support.
- Avoid broad rewrites; preserve existing telemetry and profiling comparability.
