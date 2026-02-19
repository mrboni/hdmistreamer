#!/bin/bash

# Force EDID
v4l2-ctl -d /dev/v4l-subdev2 --set-edid=file=1080p60edid
sleep 3
v4l2-ctl -d /dev/v4l-subdev2 --set-dv-bt-timings query

# Reset graph
media-ctl -d /dev/media0 -r

# Enable link
media-ctl -d /dev/media0 -l "'csi2':4 -> 'rp1-cfe-csi2_ch0':0 [1]"

# Set formats
media-ctl -d /dev/media0 --set-v4l2 "'tc358743 11-000f':0 [fmt:RGB888_1X24/1920x1080 field:none colorspace:srgb]"
media-ctl -d /dev/media0 --set-v4l2 "'csi2':0 [fmt:RGB888_1X24/1920x1080 field:none colorspace:srgb]"
media-ctl -d /dev/media0 --set-v4l2 "'csi2':4 [fmt:RGB888_1X24/1920x1080 field:none colorspace:srgb]"

# Configure video node
v4l2-ctl -d /dev/video0 -v width=1920,height=1080,pixelformat=RGB3

echo "HDMI pipeline configured."
