# Video -> NDI Deployment (RPi5 + X1300 Baseline)

This project now includes:

- `configure-hdmi.sh`: robust HDMI/EDID/media-graph bring-up with retry logic
- `scripts/prepare-video-source.sh`: source-aware startup wrapper (`hdmi-csi` or `usb-uvc`)
- `scripts/ndi_sender.py`: Python NDI sender (`gstreamer` default, `ffmpeg` optional)
- `systemd/hmdistreamer-hdmi-bringup.service`: optional standalone bring-up service
- `systemd/hmdistreamer-ndi-sender.service`: persistent NDI sender service
- `scripts/install-systemd.sh`: installs files into system paths and enables units
- `scripts/hmdistreamer-diagnostics.sh`: one-command diagnostics
- `scripts/set-mode.sh`: profile-based resolution switch helper
- `scripts/set-usb-profile.sh`: USB microscope profile helper (latency/detail)
- `scripts/usb-camera-controls.sh`: USB camera control CLI (list/get/set/preset)
- `scripts/camera-control-ui.py`: web UI backend for camera controls
- `systemd/hmdistreamer-camera-ui.service`: optional persistent camera UI service

## 1. Install dependencies

```bash
sudo ./scripts/install-deps.sh
```

This installs OS packages (including `ffmpeg`) and Python packages (`cyndilib`, `numpy`).

## 2. Install scripts + systemd services

```bash
sudo ./scripts/install-systemd.sh
```

Optional configuration:

- `/etc/hmdistreamer/hmdistreamer.env`
- `/etc/hmdistreamer/ndi_sender.toml`
- `/etc/hmdistreamer/edid/` (installed EDID profiles)

Performance note:

- Default backend is `gstreamer` for correctness/stability on this capture stack.
- `capture_backend = "ffmpeg"` is experimental here; without careful timing options it can repeat stale frames.
- Current stable X1300 camera path is native `UYVY -> NDI UYVY` end-to-end to avoid `videoconvert` overhead.
- Sender logs now include `capture->send age ms` (local queueing delay estimate) every 5 seconds.
- Sender logs also include active receiver count as `connections=N`.

## 3. Source and Mode Profiles

Input source selection (`HMDI_INPUT_KIND` in `/etc/hmdistreamer/hmdistreamer.env`):

- `hdmi-csi` (default): runs full HDMI EDID/timing/media-graph bring-up.
- `usb-uvc`: skips HDMI bring-up and prepares only USB video capture device.
- `none`: skips source prep completely (advanced/manual use).

Supported mode profiles (`HMDI_MODE` in `/etc/hmdistreamer/hmdistreamer.env`):

- `720p50`
- `720p60`
- `1080p25`
- `1080p30`
- `1080p50`
- `1080p60`
- `1080p-auto` (locks to current 1080p source timing)

Recommended default for mixed 1080p sources is:

- `HMDI_MODE=1080p-auto`
- `EDID_FILE=/etc/hmdistreamer/edid/1080p60edid` (single stable EDID identity)
- `HMDI_MEDIA_BUS_FMT=UYVY8_1X16`, `HMDI_VIDEO_PIXFMT=UYVY`
- `HMDI_NDI_FOURCC=UYVY`, `HMDI_GST_INPUT_FORMAT=UYVY`, `HMDI_GST_OUTPUT_FORMAT=UYVY`
- Avoid per-rate EDID swapping unless you explicitly need to force source behavior.

USB quick-start baseline (for development/handoff):

- `HMDI_INPUT_KIND=usb-uvc`
- `HMDI_VIDEO_DEVICE=/dev/videoX` (your webcam node)
- Set sender dimensions/fps explicitly (`HMDI_WIDTH`, `HMDI_HEIGHT`, `HMDI_FPS_NUM`, `HMDI_FPS_DEN`) if no runtime env is written.
- Keep `HMDI_NDI_FOURCC=UYVY`, `HMDI_GST_INPUT_FORMAT=UYVY`, `HMDI_GST_OUTPUT_FORMAT=UYVY` when device supports native UYVY.
- If webcam requires decode/transforms (for example MJPEG), use `HMDI_GST_SOURCE_PIPELINE` override.

USB microscope quick-start (Plugable Digital Microscope class devices):

```bash
sudo hmdistreamer-set-usb-profile microscope-latency --device /dev/video0
# or for highest detail:
sudo hmdistreamer-set-usb-profile microscope-detail --device /dev/video0
```

This configures:

- `HMDI_INPUT_KIND=usb-uvc`
- low-latency sender knobs (`appsink=1`, leaky queue, async send, stale-drop threshold)
- MJPEG decode source pipeline tuned for the selected mode
- manual camera controls by default (manual exposure + manual white balance)

Switch mode quickly:

```bash
sudo hmdistreamer-set-mode 720p60
```

The helper now clears timing/sender-dimension overrides by default, but preserves
`EDID_FILE` so fixed-EDID setups remain stable across mode changes.
Use `--clear-edid-override` if you intentionally want profile-driven EDID switching.
Use `--keep-overrides` if you want to preserve all overrides.

Or edit manually and restart:

```bash
sudoedit /etc/hmdistreamer/hmdistreamer.env
sudo systemctl restart hmdistreamer-ndi-sender.service
```

`configure-hdmi.sh` writes active width/height/fps to:

- `/run/hmdistreamer/video.env`

The sender service reads that file on each start, so width/height follow selected mode automatically.

## 4. Start and monitor

```bash
sudo systemctl start hmdistreamer-ndi-sender.service
sudo systemctl status hmdistreamer-ndi-sender.service --no-pager
sudo journalctl -u hmdistreamer-ndi-sender.service -f
# HDMI mode only:
sudo journalctl -u hmdistreamer-hdmi-bringup.service -f
# Camera UI (optional):
sudo journalctl -u hmdistreamer-camera-ui.service -f
```

Low-latency knobs (`/etc/hmdistreamer/hmdistreamer.env`):

- `HMDI_APPSINK_MAX_BUFFERS=1`
- `HMDI_NDI_SEND_ASYNC=1`
- `HMDI_NDI_SAFE_COPY=0`
- `HMDI_NDI_ASYNC_SAFE_COPY=0`
- `HMDI_NDI_CLOCK_VIDEO=0`
- `HMDI_GST_USE_LEAKY_QUEUE=1`
- `HMDI_GST_QUEUE_MAX_BUFFERS=1`
- `HMDI_DROP_STALE_MS=45..120` (drops stale frames before NDI send; tune to taste)

These can reduce buffering but may increase jitter/dropped frames if the system is overloaded.

Boot behavior:

- `hmdistreamer-ndi-sender.service` starts at boot (no login required).
- Sender startup always runs `/usr/local/bin/hmdistreamer-source-prepare` as `ExecStartPre`.
- For `HMDI_INPUT_KIND=hdmi-csi`, source-prepare delegates to `/usr/local/bin/hmdistreamer-hdmi-bringup`.
- For `HMDI_INPUT_KIND=usb-uvc`, source-prepare validates/prepares the USB device and skips HDMI/EDID steps.
- For `HMDI_INPUT_KIND=usb-uvc`, source-prepare can also apply camera controls at startup (`HMDI_USB_APPLY_CONTROLS=1`).

## 4.3 Camera Control Web UI

Enable and start:

```bash
sudo systemctl enable --now hmdistreamer-camera-ui.service
sudo systemctl status hmdistreamer-camera-ui.service --no-pager
```

Open in browser:

- `http://<pi-ip>:8787`

Capabilities:

- enumerate controls (`v4l2-ctl --list-ctrls-menus` under the hood)
- apply control values live (auto-apply on control change)
- toggle auto-apply off if you prefer batching and explicit Apply
- apply manual/auto presets
- persist startup defaults to `/etc/hmdistreamer/hmdistreamer.env` (`HMDI_USB_SET_CTRLS`)
- restart sender service from UI
- show sender latency panel (`fps`, `connections`, `capture->send age`, `ndi_send`, `stale_drop`)

Optional security:

- set `HMDI_CAMERA_UI_TOKEN` in `/etc/hmdistreamer/hmdistreamer.env`
- UI/API clients then must send it via `X-Auth-Token`

## 4.1 Current State (2026-02-20)

Known-good behavior:

- Source-driven switching on PC input between 1080p60 and 1080p50 works when using:
  - `HMDI_MODE=1080p-auto`
  - `EDID_FILE=/etc/hmdistreamer/edid/1080p60edid`
- Avoid mode-specific EDID swapping for normal operation.
- Native `UYVY -> NDI UYVY` pipeline is now stable and low-latency in this setup.
- Repeated-column artifact previously seen at 1080p50 is no longer observed after removing RGB/YUV conversion from the active path.

Reboot-cycle validation (2026-02-20):

- Host reboot completed; service came back automatically with no manual steps.
- `hmdistreamer-ndi-sender.service` is `enabled` and `active` after boot.
- Boot instance started at `2026-02-20 14:42:40 GMT` with successful `ExecStartPre` (`hmdistreamer-source-prepare` -> HDMI bring-up path).
- `/dev/video0` post-boot format is `UYVY` at `1920x1080`.
- Sender logs post-boot show stable `50.0 fps`, `stale_drop=0`, and `capture->send age` around `~20.2 ms`.
- Baseline tag for this validated camera state: `stable-camera`.

Performance profiling snapshot (RPi 5, 4 CPU cores, source locked at 1080p60):

- Raw capture (`v4l2-ctl --stream-mmap ... --stream-count=600`) : `~59.8 fps`
- GStreamer pass-through (`UYVY -> fakesink`, 300 buffers) : `~59.4 fps`
- End-to-end sender (`gstreamer + UYVY + async + no-copy`) : `~59.9 fps`
- End-to-end sender (`gstreamer + UYVY + sync + copy`) : `~59.9 fps`
- Typical sender stage timings on native UYVY path:
  - `appsink_wait` avg `~11.4 ms`
  - `map_copy` avg `~3.2 ms`
  - `ndi_send` avg `~1.0 ms` (async) / `~8.8-9.2 ms` (sync)
  - `capture->send age` avg `~16.9-17.0 ms`

Interpretation:

- Capture node is not the bottleneck at 1080p60.
- Main bottleneck previously was colorspace/pixel-format conversion (`videoconvert`).
- Native UYVY path removes that cost and restores full-rate 1080p60 in this setup.

H.264 encoder note on this RPi 5 environment:

- Current stack does not use H.264 encoding for the NDI path.
- `ffmpeg` lists `h264_v4l2m2m`, but runtime test reports `Could not find a valid device`.
- `v4l2-ctl --list-devices` only exposes `rp1-cfe` capture nodes here, no active mem2mem encoder node.

## 4.2 USB Microscope Snapshot (2026-02-21)

Measured on this Pi with `Digital Microscope: Digital Mic` (`/dev/video0`):

- Camera capability summary:
  - MJPEG: up to `1600x1200@30`
  - YUYV: `640x480@30`, `800x600@20`, `1280x720@10`, `1600x1200@5`
- Best practical USB source path here is MJPEG decode to UYVY for both throughput and latency.
- Sender tuning that reduced queue age significantly:
  - `HMDI_NDI_SEND_ASYNC=1`
  - `HMDI_NDI_SAFE_COPY=0`
  - `HMDI_NDI_ASYNC_SAFE_COPY=0`
  - `HMDI_APPSINK_MAX_BUFFERS=1`
  - `HMDI_GST_USE_LEAKY_QUEUE=1`
  - `HMDI_GST_QUEUE_MAX_BUFFERS=1`
  - `HMDI_DROP_STALE_MS=45` (1280x720) or `70` (1600x1200)
- Observed sender steady-state (`capture->send age`, local):
  - `1280x720@30` MJPEG decode: roughly `~39 ms` avg
  - `1600x1200@30` MJPEG decode: roughly `~55 ms` avg
- Manual controls are now the default USB preset:
  - `auto_exposure=1`
  - `exposure_dynamic_framerate=0`
  - `white_balance_automatic=0`

## 5. Diagnostics

Run full diagnostics:

```bash
sudo hmdistreamer-diagnostics
```

Run throughput + latency profiling (stops sender service temporarily):

```bash
sudo hmdistreamer-profile-performance
```

The profiling summary reports both FPS and stage latency averages, including:

- `appsink_wait` (upstream wait time for next frame, includes conversion/queueing effects)
- `map_copy` (frame copy into sender buffer)
- `ndi_send` (NDI write call time)
- `frame_read` (ffmpeg backend read time, when enabled)

Include optional quick stream test when sender is stopped:

```bash
sudo systemctl stop hmdistreamer-ndi-sender.service
sudo hmdistreamer-diagnostics --quick-stream-test
sudo systemctl start hmdistreamer-ndi-sender.service
```

## 6. Quick receive-side test (user action required)

On a second machine on the same wired LAN:

1. Open NDI Studio Monitor (or another NDI receiver).
2. Look for source name set in config (default `RPi5-X1300`).
3. Confirm:
   - Discovery succeeds
   - Video is stable at the current source timing
   - No periodic dropouts/restarts

If discovery fails:

- Ensure both devices are on same L2 segment/subnet.
- Check sender logs for `No video frames received` or GStreamer errors.
- For `HMDI_INPUT_KIND=hdmi-csi`, confirm `configure-hdmi.sh` reports a valid lock.
- For `HMDI_INPUT_KIND=usb-uvc`, confirm the selected `/dev/videoX` is present and streaming.

Latency troubleshooting notes:

- `capture->send age ms` is local sender-side queueing only, not full glass-to-glass delay.
- Check `connections=N` in sender logs. Multiple connected receivers can increase overall latency/jitter.
- NDI SDK behavior note: one slow receiver connection can impact all receivers on that sender.
- If using Studio Monitor, set video mode to `Lowest Latency`.

## 7. USB Handoff

For the next agent developing a USB webcam ingest variant, use:

- `Docs/USB_UVC_Handoff.md`

That handoff consolidates current architecture, refactor points, performance guardrails, and acceptance criteria.
