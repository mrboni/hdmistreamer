# HDMI -> NDI Deployment (RPi5 + X1300)

This project now includes:

- `configure-hdmi.sh`: robust HDMI/EDID/media-graph bring-up with retry logic
- `scripts/ndi_sender.py`: Python NDI sender (`gstreamer` default, `ffmpeg` optional)
- `systemd/hmdistreamer-hdmi-bringup.service`: optional standalone bring-up service
- `systemd/hmdistreamer-ndi-sender.service`: persistent NDI sender service
- `scripts/install-systemd.sh`: installs files into system paths and enables units
- `scripts/hmdistreamer-diagnostics.sh`: one-command diagnostics
- `scripts/set-mode.sh`: profile-based resolution switch helper

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
- Current X1300 default uses `RGB -> BGRx` with `NDI RGBX` to compensate an observed channel-order quirk (red/blue swap) on this stack.
- Sender logs now include `capture->send age ms` (local queueing delay estimate) every 5 seconds.

## 3. Resolution Profiles

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
- Avoid per-rate EDID swapping unless you explicitly need to force source behavior.

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
sudo journalctl -u hmdistreamer-hdmi-bringup.service -f
sudo journalctl -u hmdistreamer-ndi-sender.service -f
```

Low-latency knobs (`/etc/hmdistreamer/hmdistreamer.env`):

- `HMDI_APPSINK_MAX_BUFFERS=1`
- `HMDI_NDI_SEND_ASYNC=0`
- `HMDI_NDI_CLOCK_VIDEO=0`

These can reduce buffering but may increase jitter/dropped frames if the system is overloaded.

Boot behavior:

- `hmdistreamer-ndi-sender.service` starts at boot (no login required).
- Sender startup always runs `/usr/local/bin/hmdistreamer-hdmi-bringup` as `ExecStartPre`, so it can recover from lock loss and replug events automatically.

## 4.1 Current State (2026-02-20)

Known-good behavior:

- Source-driven switching on PC input between 1080p60 and 1080p50 works when using:
  - `HMDI_MODE=1080p-auto`
  - `EDID_FILE=/etc/hmdistreamer/edid/1080p60edid`
- Avoid mode-specific EDID swapping for normal operation.

Known issue:

- DSLR/camera input at 1080p50 can show a repeated image column artifact.
- Camera rates tested as good in this setup: 1080p30 and 1080p60.
- Camera 1080p50 artifact remains unresolved; park this until core throughput/latency work is complete.

Performance profiling snapshot (RPi 5, 4 CPU cores, source locked at 1080p60):

- Raw capture (`v4l2-ctl --stream-mmap ... --stream-count=600`) : `~59.8 fps`
- GStreamer pass-through (`RGB -> fakesink`, 600 buffers) : `~59.6 fps`
- GStreamer conversion (`RGB -> BGRx`, 600 buffers) : `~38.6 fps`
- GStreamer conversion (`RGB -> UYVY`, 600 buffers) : `~36.2 fps`
- End-to-end sender (NDI, current service-like settings) : `~23-24 fps`
- End-to-end sender best quick variant in this test set:
  - `gstreamer + async + no-copy + UYVY` : `~25-26 fps`
  - `gstreamer + async + no-copy + RGBx` : `~24-26 fps`

Interpretation:

- Capture node is not the bottleneck at 1080p60.
- Major bottleneck #1 is colorspace/pixel-format conversion (`videoconvert`).
- Major bottleneck #2 is NDI send path cost (cyndilib/libndi + Python loop/memory handling).

H.264 encoder note on this RPi 5 environment:

- Current stack does not use H.264 encoding for the NDI path.
- `ffmpeg` lists `h264_v4l2m2m`, but runtime test reports `Could not find a valid device`.
- `v4l2-ctl --list-devices` only exposes `rp1-cfe` capture nodes here, no active mem2mem encoder node.

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
- Confirm `configure-hdmi.sh` reports a valid lock for the active source mode.
