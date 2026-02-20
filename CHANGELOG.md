# Changelog

## 2026-02-20

### Added

- `HMDI_MODE=1080p-auto` for source-driven 1080p timing lock (no forced fps/pixelclock match).
- Sender latency telemetry in `scripts/ndi_sender.py`:
  - `capture->send age ms`
  - per-stage averages for `appsink_wait`, `map_copy`, `ndi_send`, and `frame_read` (ffmpeg backend).
- `scripts/profile-performance.sh` and installed command `hmdistreamer-profile-performance` for repeatable throughput + latency profiling.
- `ndi_safe_copy` and `ndi_async_safe_copy` config entries in `config/ndi_sender.toml.example` and `config/hmdistreamer.env.example`.
- Current-state and profiling notes in deployment docs, including known camera 1080p50 artifact behavior.

### Changed

- `configure-hdmi.sh`
  - HDMI lock validation now includes FPS (not just width/height/pixelclock).
  - Runtime sender dimensions/fps now come from detected lock values.
  - `1080p50` profile uses stable `1080p60edid` identity by default to avoid source capability churn.
  - Runtime env no longer writes `HMDI_MODE` into `/run/hmdistreamer/video.env`.
- `scripts/set-mode.sh`
  - preserves `EDID_FILE` by default when switching mode profiles.
  - adds `--clear-edid-override` to intentionally re-enable profile-driven EDID selection.
  - clears stale `/run/hmdistreamer/video.env` when switching modes.
- `systemd/hmdistreamer-ndi-sender.service`
  - environment file order prioritizes static config (`/etc`) before runtime dimensions (`/run`).
- Default example config updated toward stable mixed-source operation:
  - `HMDI_MODE=1080p-auto`
  - fixed EDID recommendation: `/etc/hmdistreamer/edid/1080p60edid`.

### Fixed

- 1080p50/1080p60 profile ambiguity caused by shared pixelclock now resolved via explicit FPS matching.
- Mode switch failures caused by stale runtime mode state overriding static config.
- EDID identity flip during 50/60 profile switching when using defaults.

### Known Issues

- Some DSLR/camera 1080p50 outputs still show repeated-column artifacts while 1080p30/60 remain clean in current stack.
