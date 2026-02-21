# Raspberry Pi 5 + X1300 HDMI → NDI Appliance

## Engineering Handoff Document for Code CLI

> Historical bring-up notes from early project phases.
> For current operational guidance use `Docs/Deployment.md`.
> For USB webcam extension work use `Docs/USB_UVC_Handoff.md`.

**Date:** 2026-02-18 **Platform:** Raspberry Pi 5\
**OS:** Raspberry Pi OS (64-bit) -- Trixie\
**Hardware:** Geekworm X1300 (TC358743 HDMI → CSI bridge)\
**Target Mode:** 1920x1080 @ 60fps (Fixed EDID)

------------------------------------------------------------------------

# 1. Current Status

We have successfully achieved:

-   Stable HDMI lock at 1080p60

-   Correct EDID injection

-   Correct media graph configuration

-   Successful raw streaming via:

        v4l2-ctl --stream-mmap

-   Successful GStreamer streaming using:

        format=UYVY

The capture stack is now stable and reproducible.

------------------------------------------------------------------------

# 2. Required Bring-Up Procedure (Per Boot)

## 2.1 Inject EDID and Query Timings

``` bash
v4l2-ctl -d /dev/v4l-subdev2 --set-edid=file=1080p60edid
sleep 3
v4l2-ctl -d /dev/v4l-subdev2 --set-dv-bt-timings query
```

Verify HDMI lock:

``` bash
v4l2-ctl -d /dev/v4l-subdev2 --query-dv-timings
```

Expected: - Active width: 1920 - Active height: 1080 - Pixelclock:
148500000 Hz

------------------------------------------------------------------------

## 2.2 Reset and Configure Media Graph

``` bash
media-ctl -d /dev/media0 -r
```

Enable link:

``` bash
media-ctl -d /dev/media0   -l "'csi2':4 -> 'rp1-cfe-csi2_ch0':0 [1]"
```

Propagate formats:

``` bash
media-ctl -d /dev/media0   --set-v4l2 "'tc358743 11-000f':0 [fmt:UYVY8_1X16/1920x1080 field:none colorspace:srgb]"

media-ctl -d /dev/media0   --set-v4l2 "'csi2':0 [fmt:UYVY8_1X16/1920x1080 field:none colorspace:srgb]"

media-ctl -d /dev/media0   --set-v4l2 "'csi2':4 [fmt:UYVY8_1X16/1920x1080 field:none colorspace:srgb]"
```

------------------------------------------------------------------------

## 2.3 Configure Video Node

``` bash
v4l2-ctl -d /dev/video0   -v width=1920,height=1080,pixelformat=UYVY
```

------------------------------------------------------------------------

## 2.4 Validate Streaming

``` bash
v4l2-ctl -d /dev/video0   --stream-mmap=4 --stream-count=60 --stream-to=/dev/null
```

If frames print as `<<<<<<<<`, streaming is functional.

------------------------------------------------------------------------

# 3. Known Critical Requirements

-   Prefer native `format=UYVY` in GStreamer/NDI to avoid conversion overhead.
-   EDID must be injected every boot.
-   Media graph must be configured before streaming.
-   If HDMI is unplugged, pipeline must be reinitialised.

------------------------------------------------------------------------

# 4. Current Working GStreamer Test

``` bash
gst-launch-1.0 v4l2src device=/dev/video0 io-mode=mmap !   video/x-raw,format=UYVY,width=1920,height=1080,framerate=60/1 !   fakesink
```

------------------------------------------------------------------------

# 5. Objective: Convert into Embedded HDMI → NDI Appliance

## Phase A --- Auto-start Media Configuration

Goal: - Convert bring-up procedure into a systemd service. - Ensure it
runs at boot. - Ensure HDMI signal detection before format
propagation. - Add retry logic if HDMI is not locked.

Deliverables: - systemd service unit file - Robust shell bring-up
script - Logging via journalctl

------------------------------------------------------------------------

## Phase B --- Resolution Awareness (Future, Not Now)

For now: - Fixed EDID = 1080p60.

Later: - Support multiple EDID files. - Auto-detect DV timings. -
Dynamically propagate format through media graph. - Adjust /dev/video0
format automatically.

------------------------------------------------------------------------

## Phase C --- NDI Sender Service

Goal: - Stable full NDI (High Bandwidth) over wired GigE. - Zero manual
startup steps.

Architecture:

    v4l2src (UYVY)
    → GStreamer appsink
    → Python NDI sender
    → Gigabit Ethernet

Requirements: - Auto-start at boot - Restart on failure - Low latency
(minimise conversions) - Clean shutdown handling

Deliverables: - Python NDI sender module - systemd service for NDI
sender - Optional config file for name, resolution, framerate

------------------------------------------------------------------------

# 6. Future Enhancements (Not Required Now)

-   Hardware encode (H.264) optional mode
-   NDI\|HX support (licensing dependent)
-   Dynamic resolution switching
-   Hotplug detection
-   Web UI for configuration
-   Device identity / hostname → NDI name mapping

------------------------------------------------------------------------

# 7. Constraints

-   Keep 1080p60 fixed for now.
-   Wired GigE only.
-   No WiFi optimisation required.
-   Stability preferred over feature expansion.

------------------------------------------------------------------------

# 8. Final Goal

Create a fully self-contained Raspberry Pi 5 HDMI → NDI encoder
appliance that:

-   Boots
-   Locks HDMI
-   Configures media graph
-   Starts NDI stream automatically
-   Requires zero manual commands

------------------------------------------------------------------------

# End of Document
