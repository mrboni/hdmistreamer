# HDMI -> NDI Deployment (RPi5 + X1300)

This project now includes:

- `configure-hdmi.sh`: robust HDMI/EDID/media-graph bring-up with retry logic
- `scripts/ndi_sender.py`: Python NDI sender (`ffmpeg->rawvideo` default, `gstreamer` fallback)
- `systemd/hmdistreamer-hdmi-bringup.service`: boot-time capture bring-up
- `systemd/hmdistreamer-ndi-sender.service`: persistent NDI sender service
- `scripts/install-systemd.sh`: installs files into system paths and enables units

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

Performance note:

- Default backend is `ffmpeg` for higher FPS at 1080p60.
- `capture_backend = "gstreamer"` remains available for fallback/testing.

## 3. Start and monitor

```bash
sudo systemctl start hmdistreamer-ndi-sender.service
sudo systemctl status hmdistreamer-ndi-sender.service --no-pager
sudo journalctl -u hmdistreamer-hdmi-bringup.service -f
sudo journalctl -u hmdistreamer-ndi-sender.service -f
```

## 4. Quick receive-side test (user action required)

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
