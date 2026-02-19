# HDMI -> NDI Deployment (RPi5 + X1300)

This project now includes:

- `configure-hdmi.sh`: robust HDMI/EDID/media-graph bring-up with retry logic
- `scripts/ndi_sender.py`: Python NDI sender (`gstreamer` default, `ffmpeg` optional)
- `systemd/hmdistreamer-hdmi-bringup.service`: boot-time capture bring-up
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

## 3. Resolution Profiles

Supported mode profiles (`HMDI_MODE` in `/etc/hmdistreamer/hmdistreamer.env`):

- `720p60`
- `1080p25`
- `1080p30`
- `1080p50`
- `1080p60`

Switch mode quickly:

```bash
sudo hmdistreamer-set-mode 720p60
```

The helper clears explicit `EDID_FILE`/timing/sender-dimension overrides by default so profile changes apply cleanly.
Use `--keep-overrides` if you want to preserve manual overrides.

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

## 5. Diagnostics

Run full diagnostics:

```bash
sudo hmdistreamer-diagnostics
```

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
   - Video is stable at 1080p60
   - No periodic dropouts/restarts

If discovery fails:

- Ensure both devices are on same L2 segment/subnet.
- Check sender logs for `No video frames received` or GStreamer errors.
- Confirm `configure-hdmi.sh` still reports locked 1920x1080 @ 148500000.
