# Changelog

## 2026-02-20

### Added

- `HMDI_MODE=1080p-auto` for source-driven 1080p timing lock (no forced fps/pixelclock match).
- Sender latency telemetry in `scripts/ndi_sender.py`:
  - `capture->send age ms`
  - per-stage averages for `appsink_wait`, `map_copy`, `ndi_send`, and `frame_read` (ffmpeg backend).
- `scripts/profile-performance.sh` and installed command `hmdistreamer-profile-performance` for repeatable throughput + latency profiling.
- `ndi_safe_copy` and `ndi_async_safe_copy` config entries in `config/ndi_sender.toml.example` and `config/hmdistreamer.env.example`.
- Current-state and profiling notes in deployment docs, including reboot-cycle validation on `2026-02-20`.

### Changed

- `configure-hdmi.sh`
  - HDMI lock validation now includes FPS (not just width/height/pixelclock).
  - Runtime sender dimensions/fps now come from detected lock values.
  - `1080p50` profile uses stable `1080p60edid` identity by default to avoid source capability churn.
  - Runtime env no longer writes `HMDI_MODE` into `/run/hmdistreamer/video.env`.
  - Media graph/video node formats are now configurable via env and default to native UYVY:
    - `HMDI_MEDIA_BUS_FMT=UYVY8_1X16`
    - `HMDI_MEDIA_FIELD=none`
    - `HMDI_MEDIA_COLORSPACE=srgb`
    - `HMDI_VIDEO_PIXFMT=UYVY`
- `scripts/set-mode.sh`
  - preserves `EDID_FILE` by default when switching mode profiles.
  - adds `--clear-edid-override` to intentionally re-enable profile-driven EDID selection.
  - clears stale `/run/hmdistreamer/video.env` when switching modes.
- `systemd/hmdistreamer-ndi-sender.service`
  - environment file order prioritizes static config (`/etc`) before runtime dimensions (`/run`).
- Default example config updated toward stable mixed-source operation:
  - `HMDI_MODE=1080p-auto`
  - fixed EDID recommendation: `/etc/hmdistreamer/edid/1080p60edid`.
  - default sender/capture path switched to native UYVY (`HMDI_NDI_FOURCC=UYVY`, `HMDI_GST_INPUT_FORMAT=UYVY`, `HMDI_GST_OUTPUT_FORMAT=UYVY`).
- `scripts/ndi_sender.py`
  - sender defaults now target UYVY direct path.
  - ffmpeg backend now supports `ndi_fourcc=UYVY` with `ffmpeg_pix_fmt=uyvy422`.
- `scripts/profile-performance.sh`
  - capture-stage tests now key off the active native source format instead of assuming RGB.
  - ffmpeg sender variant updated to `UYVY`.

### Fixed

- 1080p50/1080p60 profile ambiguity caused by shared pixelclock now resolved via explicit FPS matching.
- Mode switch failures caused by stale runtime mode state overriding static config.
- EDID identity flip during 50/60 profile switching when using defaults.
- Repeated-column artifact at 1080p50 is resolved in the active default path by using native `UYVY -> NDI UYVY` (no conversion).

### Known Issues

- No active repeated-column issue observed in current native UYVY deployment path; keep prior notes for historical context.
