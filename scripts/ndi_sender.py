#!/usr/bin/env python3

from __future__ import annotations

import argparse
from fractions import Fraction
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    import tomli as tomllib  # type: ignore[no-redef]

try:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
except Exception as exc:  # pragma: no cover - runtime dependency check
    raise SystemExit(
        "Failed to import GStreamer bindings. Install python3-gi and python3-gst-1.0."
    ) from exc

try:
    from cyndilib import FourCC, Sender, VideoSendFrame
except Exception as exc:  # pragma: no cover - runtime dependency check
    raise SystemExit(
        "Failed to import cyndilib. Install with `pip install cyndilib`."
    ) from exc


@dataclass
class SenderConfig:
    ndi_name: str = "RPi5-X1300"
    video_device: str = "/dev/video0"
    width: int = 1920
    height: int = 1080
    fps_num: int = 60
    fps_den: int = 1
    sample_timeout_sec: float = 0.5
    no_frame_restart_sec: float = 3.0
    appsink_max_buffers: int = 2
    gst_io_mode: str = "mmap"
    log_level: str = "INFO"


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def convert_config_value(template: Any, raw: Any) -> Any:
    if isinstance(template, bool):
        return parse_bool(str(raw))
    if isinstance(template, int):
        return int(raw)
    if isinstance(template, float):
        return float(raw)
    return str(raw)


def load_toml_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as fp:
        parsed = tomllib.load(fp)
    if "ndi_sender" in parsed and isinstance(parsed["ndi_sender"], dict):
        return dict(parsed["ndi_sender"])
    return dict(parsed)


def build_config(config_path: Path) -> SenderConfig:
    cfg = SenderConfig()
    toml_values = load_toml_config(config_path)
    for field_name, default_value in cfg.__dict__.items():
        if field_name in toml_values:
            setattr(cfg, field_name, convert_config_value(default_value, toml_values[field_name]))

    env_overrides = {
        "HMDI_NDI_NAME": "ndi_name",
        "HMDI_VIDEO_DEVICE": "video_device",
        "HMDI_WIDTH": "width",
        "HMDI_HEIGHT": "height",
        "HMDI_FPS_NUM": "fps_num",
        "HMDI_FPS_DEN": "fps_den",
        "HMDI_SAMPLE_TIMEOUT_SEC": "sample_timeout_sec",
        "HMDI_NO_FRAME_RESTART_SEC": "no_frame_restart_sec",
        "HMDI_APPSINK_MAX_BUFFERS": "appsink_max_buffers",
        "HMDI_GST_IO_MODE": "gst_io_mode",
        "HMDI_LOG_LEVEL": "log_level",
    }

    for env_name, field_name in env_overrides.items():
        raw = os.getenv(env_name)
        if raw is None:
            continue
        template = getattr(cfg, field_name)
        setattr(cfg, field_name, convert_config_value(template, raw))

    return cfg


def build_pipeline(cfg: SenderConfig) -> str:
    return (
        f"v4l2src device={cfg.video_device} io-mode={cfg.gst_io_mode} do-timestamp=true ! "
        f"video/x-raw,format=RGB,width={cfg.width},height={cfg.height},framerate={cfg.fps_num}/{cfg.fps_den} ! "
        "videoconvert n-threads=4 ! "
        "video/x-raw,format=BGRx ! "
        f"appsink name=framesink emit-signals=false sync=false drop=true max-buffers={cfg.appsink_max_buffers}"
    )


class HDMIToNDISender:
    def __init__(self, cfg: SenderConfig, stop_event: Event) -> None:
        self.cfg = cfg
        self.stop_event = stop_event
        self.expected_bytes = cfg.width * cfg.height * 4
        self.frame_buffer = bytearray(self.expected_bytes)
        self.frame_buffer_view = memoryview(self.frame_buffer)
        self.pipeline: Gst.Pipeline | None = None
        self.appsink: Gst.Element | None = None
        self.bus: Gst.Bus | None = None
        self.sender: Sender | None = None
        self.video_frame: VideoSendFrame | None = None

    def start(self) -> None:
        self.sender = Sender(
            self.cfg.ndi_name,
            clock_video=True,
            clock_audio=False,
        )

        self.video_frame = VideoSendFrame()
        self.video_frame.set_resolution(self.cfg.width, self.cfg.height)
        self.video_frame.set_frame_rate(Fraction(self.cfg.fps_num, self.cfg.fps_den))
        self.video_frame.set_fourcc(FourCC.BGRX)
        self.sender.set_video_frame(self.video_frame)
        self.sender.open()

        pipeline_text = build_pipeline(self.cfg)
        logging.info("Starting capture pipeline: %s", pipeline_text)
        self.pipeline = Gst.parse_launch(pipeline_text)
        if self.pipeline is None:
            raise RuntimeError("Failed to build GStreamer pipeline")

        self.appsink = self.pipeline.get_by_name("framesink")
        if self.appsink is None:
            raise RuntimeError("Could not access appsink from pipeline")

        self.bus = self.pipeline.get_bus()
        state_result = self.pipeline.set_state(Gst.State.PLAYING)
        if state_result == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Could not transition GStreamer pipeline to PLAYING")

    def stop(self) -> None:
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = None
        self.appsink = None
        self.bus = None
        if self.sender is not None:
            self.sender.close()
            self.sender = None
        if self.video_frame is not None:
            self.video_frame.destroy()
            self.video_frame = None

    def check_bus(self) -> None:
        if self.bus is None:
            return
        while True:
            message = self.bus.timed_pop_filtered(
                0, Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.WARNING
            )
            if message is None:
                return
            if message.type == Gst.MessageType.WARNING:
                warn, dbg = message.parse_warning()
                logging.warning("GStreamer warning: %s (%s)", warn, dbg)
                continue
            if message.type == Gst.MessageType.ERROR:
                err, dbg = message.parse_error()
                raise RuntimeError(f"GStreamer error: {err} ({dbg})")
            if message.type == Gst.MessageType.EOS:
                raise RuntimeError("GStreamer pipeline ended (EOS)")

    def run(self) -> None:
        if self.appsink is None or self.video_frame is None or self.sender is None:
            raise RuntimeError("Sender is not initialized")

        timeout_ns = int(self.cfg.sample_timeout_sec * Gst.SECOND)
        last_frame_time = time.monotonic()
        fps_window_start = last_frame_time
        fps_window_count = 0

        logging.info("NDI sender '%s' is running", self.cfg.ndi_name)
        while not self.stop_event.is_set():
            self.check_bus()
            sample = self.appsink.emit("try-pull-sample", timeout_ns)
            now = time.monotonic()
            if sample is None:
                if now - last_frame_time > self.cfg.no_frame_restart_sec:
                    raise RuntimeError(
                        f"No video frames received for {self.cfg.no_frame_restart_sec:.1f}s"
                    )
                continue

            buffer = sample.get_buffer()
            if buffer is None:
                continue

            mapped, map_info = buffer.map(Gst.MapFlags.READ)
            if not mapped:
                continue

            try:
                mapped_size = getattr(map_info, "size", len(map_info.data))
                if mapped_size != self.expected_bytes:
                    logging.warning(
                        "Discarding frame with unexpected size: got %d bytes, expected %d",
                        mapped_size,
                        self.expected_bytes,
                    )
                    continue

                self.frame_buffer_view[:] = map_info.data
                ok = self.sender.write_video(self.frame_buffer)
                if not ok:
                    logging.warning("NDI sender declined frame write")
            finally:
                buffer.unmap(map_info)

            last_frame_time = now
            fps_window_count += 1
            if now - fps_window_start >= 5.0:
                fps = fps_window_count / (now - fps_window_start)
                logging.info("Sending %.1f fps", fps)
                fps_window_start = now
                fps_window_count = 0


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HDMI to NDI sender")
    parser.add_argument(
        "--config",
        default="/etc/hmdistreamer/ndi_sender.toml",
        help="Path to TOML config file",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    cfg = build_config(Path(args.config))
    configure_logging(cfg.log_level)

    stop_event = Event()

    def handle_signal(signum: int, _frame: Any) -> None:
        logging.info("Received signal %s, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    Gst.init(None)

    sender = HDMIToNDISender(cfg, stop_event)
    try:
        sender.start()
        sender.run()
    except Exception:
        logging.exception("NDI sender exited with error")
        return 1
    finally:
        sender.stop()

    logging.info("NDI sender stopped cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
