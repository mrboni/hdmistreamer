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
  - startup USB control application (`HMDI_USB_APPLY_CONTROLS`, `HMDI_USB_CONTROL_PRESET`, `HMDI_USB_SET_CTRLS`)
- Sender: `/usr/local/bin/hmdistreamer-ndi-sender`
  - `capture_backend=gstreamer` (default, preferred)
  - latency telemetry in logs (`capture->send age`, step timings)
  - optional `HMDI_GST_SOURCE_PIPELINE` for custom webcam graphs (MJPEG decode, etc)
- Profiling helper: `/usr/local/bin/hmdistreamer-profile-performance`
- USB profile helper: `/usr/local/bin/hmdistreamer-set-usb-profile`
- USB control helper: `/usr/local/bin/hmdistreamer-usb-controls`
- USB control web UI service: `/usr/local/bin/hmdistreamer-camera-ui` (`hmdistreamer-camera-ui.service`)

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
HMDI_WIDTH=1280
HMDI_HEIGHT=720
HMDI_FPS_NUM=30
HMDI_FPS_DEN=1
HMDI_NDI_FOURCC=UYVY
HMDI_GST_INPUT_FORMAT=UYVY
HMDI_GST_OUTPUT_FORMAT=UYVY
HMDI_GST_SOURCE_PIPELINE=v4l2src device=/dev/videoX io-mode=mmap do-timestamp=true ! image/jpeg,width=1280,height=720,framerate=30/1 ! jpegdec ! videoconvert n-threads=4 ! video/x-raw,format=UYVY,width=1280,height=720,framerate=30/1
HMDI_APPSINK_MAX_BUFFERS=1
HMDI_GST_USE_LEAKY_QUEUE=1
HMDI_GST_QUEUE_MAX_BUFFERS=1
HMDI_NDI_SEND_ASYNC=1
HMDI_NDI_SAFE_COPY=0
HMDI_NDI_ASYNC_SAFE_COPY=0
HMDI_NDI_CLOCK_VIDEO=0
HMDI_DROP_STALE_MS=45
HMDI_USB_APPLY_CONTROLS=1
HMDI_USB_CONTROL_PRESET=manual
HMDI_USB_SET_CTRLS=auto_exposure=1,exposure_time_absolute=157,exposure_dynamic_framerate=0,white_balance_automatic=0,white_balance_temperature=4600,power_line_frequency=2,gain=0
```

For higher detail on this microscope:

```bash
sudo hmdistreamer-set-usb-profile microscope-detail --device /dev/video0
```

## Performance Guardrails

For this USB microscope class on this hardware:

- Throughput target: near 30 fps steady-state
- Latency target: `capture->send age` roughly `~39 ms` at `1280x720` or `~55 ms` at `1600x1200`
- Stability target: no service restarts, no persistent stale-drop growth

## Acceptance Checklist

- Service starts automatically after reboot with USB config.
- Stable NDI discovery and image continuity for 30+ minutes.
- Profiling report captured and committed for chosen USB pipeline.
- Any residual limitations documented with exact camera model and mode.

## Notes

- Keep HDMI/X1300 baseline behavior intact while adding USB support.
- Avoid broad rewrites; preserve existing telemetry and profiling comparability.
- Manual controls can be tuned live with:
  - `hmdistreamer-usb-controls preset manual`
  - `hmdistreamer-usb-controls set exposure_time_absolute=140 white_balance_temperature=4700 gain=3`
- Or via browser UI at `http://<pi-ip>:8787` when `hmdistreamer-camera-ui.service` is enabled.
  - UI auto-applies control changes by default and includes a sender latency panel.
  - UI presets/persist are now env-configurable for branch portability:
    - disable with `HMDI_CAMERA_UI_ENABLE_PRESETS=0` and/or `HMDI_CAMERA_UI_ENABLE_PERSIST=0`
    - remap persistence keys with `HMDI_CAMERA_UI_PERSIST_*` variables when not using USB startup keys.
