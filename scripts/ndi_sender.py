#!/usr/bin/env python3

from __future__ import annotations

import argparse
from fractions import Fraction
import logging
import os
import select
import signal
import subprocess
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
    from cyndilib import FourCC, Sender, VideoSendFrame
except Exception as exc:  # pragma: no cover - runtime dependency check
    raise SystemExit(
        "Failed to import cyndilib. Install with `pip install cyndilib`."
    ) from exc

Gst = None


@dataclass
class SenderConfig:
    ndi_name: str = "RPi5-X1300"
    ndi_fourcc: str = "UYVY"
    video_device: str = "/dev/video0"
    width: int = 1920
    height: int = 1080
    fps_num: int = 60
    fps_den: int = 1
    sample_timeout_sec: float = 0.5
    no_frame_restart_sec: float = 3.0
    capture_backend: str = "gstreamer"
    ndi_send_async: bool = True
    ndi_safe_copy: bool = True
    ndi_async_safe_copy: bool = True
    ndi_clock_video: bool = True
    ffmpeg_path: str = "ffmpeg"
    ffmpeg_loglevel: str = "error"
    ffmpeg_input_format: str = "uyvy422"
    ffmpeg_pix_fmt: str = "uyvy422"
    ffmpeg_vsync: int = 0
    ffmpeg_threads: int = 4
    ffmpeg_thread_queue_size: int = 512
    appsink_max_buffers: int = 2
    gst_io_mode: str = "mmap"
    gst_convert_threads: int = 4
    gst_input_format: str = "UYVY"
    gst_output_format: str = "UYVY"
    gst_use_leaky_queue: bool = False
    gst_queue_max_buffers: int = 1
    drop_stale_ms: float = 0.0
    log_level: str = "INFO"


@dataclass
class WindowMetric:
    count: int = 0
    total: float = 0.0
    minimum: float = float("inf")
    maximum: float = 0.0

    def add(self, value: float) -> None:
        self.count += 1
        self.total += value
        if value < self.minimum:
            self.minimum = value
        if value > self.maximum:
            self.maximum = value

    def avg(self) -> float:
        if self.count == 0:
            return 0.0
        return self.total / self.count

    def summary(self) -> tuple[float, float, float]:
        if self.count == 0:
            return (0.0, 0.0, 0.0)
        return (self.minimum, self.avg(), self.maximum)

    def reset(self) -> None:
        self.count = 0
        self.total = 0.0
        self.minimum = float("inf")
        self.maximum = 0.0


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
        "HMDI_NDI_FOURCC": "ndi_fourcc",
        "HMDI_VIDEO_DEVICE": "video_device",
        "HMDI_WIDTH": "width",
        "HMDI_HEIGHT": "height",
        "HMDI_FPS_NUM": "fps_num",
        "HMDI_FPS_DEN": "fps_den",
        "HMDI_SAMPLE_TIMEOUT_SEC": "sample_timeout_sec",
        "HMDI_NO_FRAME_RESTART_SEC": "no_frame_restart_sec",
        "HMDI_CAPTURE_BACKEND": "capture_backend",
        "HMDI_NDI_SEND_ASYNC": "ndi_send_async",
        "HMDI_NDI_SAFE_COPY": "ndi_safe_copy",
        "HMDI_NDI_ASYNC_SAFE_COPY": "ndi_async_safe_copy",
        "HMDI_NDI_CLOCK_VIDEO": "ndi_clock_video",
        "HMDI_FFMPEG_PATH": "ffmpeg_path",
        "HMDI_FFMPEG_LOGLEVEL": "ffmpeg_loglevel",
        "HMDI_FFMPEG_INPUT_FORMAT": "ffmpeg_input_format",
        "HMDI_FFMPEG_PIX_FMT": "ffmpeg_pix_fmt",
        "HMDI_FFMPEG_VSYNC": "ffmpeg_vsync",
        "HMDI_FFMPEG_THREADS": "ffmpeg_threads",
        "HMDI_FFMPEG_THREAD_QUEUE_SIZE": "ffmpeg_thread_queue_size",
        "HMDI_APPSINK_MAX_BUFFERS": "appsink_max_buffers",
        "HMDI_GST_IO_MODE": "gst_io_mode",
        "HMDI_GST_CONVERT_THREADS": "gst_convert_threads",
        "HMDI_GST_INPUT_FORMAT": "gst_input_format",
        "HMDI_GST_OUTPUT_FORMAT": "gst_output_format",
        "HMDI_GST_USE_LEAKY_QUEUE": "gst_use_leaky_queue",
        "HMDI_GST_QUEUE_MAX_BUFFERS": "gst_queue_max_buffers",
        "HMDI_DROP_STALE_MS": "drop_stale_ms",
        "HMDI_LOG_LEVEL": "log_level",
    }

    for env_name, field_name in env_overrides.items():
        raw = os.getenv(env_name)
        if raw is None:
            continue
        template = getattr(cfg, field_name)
        setattr(cfg, field_name, convert_config_value(template, raw))

    return cfg


GST_FORMAT_CANONICAL = {
    "rgb": "RGB",
    "bgr": "BGR",
    "rgbx": "RGBx",
    "bgrx": "BGRx",
    "uyvy": "UYVY",
}

FOURCC_GST_FORMAT = {
    "RGBX": "RGBx",
    "BGRX": "BGRx",
    "UYVY": "UYVY",
}

FOURCC_BYTES_PER_PIXEL = {
    "RGBX": 4,
    "BGRX": 4,
    "UYVY": 2,
}

FFMPEG_PIX_FMT_FOR_FOURCC = {
    "RGBX": "rgb0",
    "BGRX": "bgr0",
    "UYVY": "uyvy422",
}


def canonical_gst_format(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in GST_FORMAT_CANONICAL:
        supported = ", ".join(sorted(GST_FORMAT_CANONICAL))
        raise ValueError(f"Unsupported gst format '{value}'. Supported: {supported}")
    return GST_FORMAT_CANONICAL[normalized]


def normalize_fourcc_name(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in FOURCC_BYTES_PER_PIXEL:
        supported = ", ".join(sorted(FOURCC_BYTES_PER_PIXEL))
        raise ValueError(f"Unsupported ndi_fourcc '{value}'. Supported: {supported}")
    return normalized


def build_pipeline(cfg: SenderConfig) -> str:
    input_format = canonical_gst_format(cfg.gst_input_format)
    output_format = canonical_gst_format(cfg.gst_output_format)

    input_caps = (
        f"video/x-raw,format={input_format},width={cfg.width},height={cfg.height},"
        f"framerate={cfg.fps_num}/{cfg.fps_den}"
    )
    output_caps = f"video/x-raw,format={output_format}"
    appsink = (
        f"appsink name=framesink emit-signals=false sync=false drop=true "
        f"max-buffers={cfg.appsink_max_buffers}"
    )
    queue_stage = ""
    if cfg.gst_use_leaky_queue:
        queue_stage = (
            "queue leaky=downstream "
            f"max-size-buffers={cfg.gst_queue_max_buffers} max-size-time=0 max-size-bytes=0 ! "
        )

    if input_format == output_format:
        return (
            f"v4l2src device={cfg.video_device} io-mode={cfg.gst_io_mode} do-timestamp=true ! "
            f"{input_caps} ! {queue_stage}{appsink}"
        )

    return (
        f"v4l2src device={cfg.video_device} io-mode={cfg.gst_io_mode} do-timestamp=true ! "
        f"{input_caps} ! "
        f"{queue_stage}"
        f"videoconvert n-threads={cfg.gst_convert_threads} ! "
        f"{output_caps} ! "
        f"{queue_stage}{appsink}"
    )


def ensure_gst() -> Any:
    global Gst
    if Gst is not None:
        return Gst
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst as _Gst
    except Exception as exc:  # pragma: no cover - runtime dependency check
        raise RuntimeError(
            "Failed to import GStreamer bindings. Install python3-gi and python3-gst-1.0."
        ) from exc
    _Gst.init(None)
    Gst = _Gst
    return Gst


class BaseHDMIToNDISender:
    def __init__(self, cfg: SenderConfig, stop_event: Event) -> None:
        self.cfg = cfg
        self.stop_event = stop_event
        self.ndi_fourcc_name = normalize_fourcc_name(cfg.ndi_fourcc)
        self.expected_bytes = cfg.width * cfg.height * FOURCC_BYTES_PER_PIXEL[self.ndi_fourcc_name]
        self.frame_buffer = bytearray(self.expected_bytes)
        self.frame_buffer_view = memoryview(self.frame_buffer)
        self.sender: Sender | None = None
        self.video_frame: VideoSendFrame | None = None

    def start_ndi(self) -> None:
        self.sender = Sender(
            self.cfg.ndi_name,
            clock_video=self.cfg.ndi_clock_video,
            clock_audio=False,
        )

        self.video_frame = VideoSendFrame()
        self.video_frame.set_resolution(self.cfg.width, self.cfg.height)
        self.video_frame.set_frame_rate(Fraction(self.cfg.fps_num, self.cfg.fps_den))
        self.video_frame.set_fourcc(getattr(FourCC, self.ndi_fourcc_name))
        self.sender.set_video_frame(self.video_frame)
        self.sender.open()

    def stop_ndi(self) -> None:
        if self.sender is not None:
            self.sender.close()
            self.sender = None
        if self.video_frame is not None:
            self.video_frame.destroy()
            self.video_frame = None

    def send_frame(self, frame_data: Any | None = None) -> None:
        if self.sender is None:
            raise RuntimeError("NDI sender is not initialized")
        payload = self.frame_buffer if frame_data is None else frame_data
        if self.cfg.ndi_safe_copy and (not self.cfg.ndi_send_async or self.cfg.ndi_async_safe_copy):
            # cyndilib may continue reading frame memory after write_video()/write_video_async
            # returns. Use a dedicated writable copy per frame to prevent data races and
            # partial-frame corruption from buffer reuse.
            payload = bytearray(payload)
        if self.cfg.ndi_send_async:
            ok = self.sender.write_video_async(payload)
        else:
            ok = self.sender.write_video(payload)
        if not ok:
            logging.warning("NDI sender declined frame write")


class GStreamerHDMIToNDISender(BaseHDMIToNDISender):
    def __init__(self, cfg: SenderConfig, stop_event: Event) -> None:
        super().__init__(cfg, stop_event)
        self.pipeline = None
        self.appsink = None
        self.bus = None

    def start(self) -> None:
        gst = ensure_gst()
        output_format = canonical_gst_format(self.cfg.gst_output_format)
        expected_format = FOURCC_GST_FORMAT[self.ndi_fourcc_name]
        if output_format != expected_format:
            logging.warning(
                "gst_output_format=%s does not match ndi_fourcc=%s (expected %s). "
                "Using mismatch intentionally can correct channel-order quirks on some capture drivers.",
                output_format,
                self.ndi_fourcc_name,
                expected_format,
            )
        self.start_ndi()
        pipeline_text = build_pipeline(self.cfg)
        logging.info("Starting capture pipeline: %s", pipeline_text)
        self.pipeline = gst.parse_launch(pipeline_text)
        if self.pipeline is None:
            raise RuntimeError("Failed to build GStreamer pipeline")

        self.appsink = self.pipeline.get_by_name("framesink")
        if self.appsink is None:
            raise RuntimeError("Could not access appsink from pipeline")

        self.bus = self.pipeline.get_bus()
        state_result = self.pipeline.set_state(gst.State.PLAYING)
        if state_result == gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Could not transition GStreamer pipeline to PLAYING")

    def stop(self) -> None:
        gst = ensure_gst()
        if self.pipeline is not None:
            self.pipeline.set_state(gst.State.NULL)
        self.pipeline = None
        self.appsink = None
        self.bus = None
        self.stop_ndi()

    def check_bus(self) -> None:
        gst = ensure_gst()
        if self.bus is None:
            return
        while True:
            message = self.bus.timed_pop_filtered(
                0, gst.MessageType.ERROR | gst.MessageType.EOS | gst.MessageType.WARNING
            )
            if message is None:
                return
            if message.type == gst.MessageType.WARNING:
                warn, dbg = message.parse_warning()
                logging.warning("GStreamer warning: %s (%s)", warn, dbg)
                continue
            if message.type == gst.MessageType.ERROR:
                err, dbg = message.parse_error()
                raise RuntimeError(f"GStreamer error: {err} ({dbg})")
            if message.type == gst.MessageType.EOS:
                raise RuntimeError("GStreamer pipeline ended (EOS)")

    def run(self) -> None:
        gst = ensure_gst()
        if self.appsink is None or self.video_frame is None or self.sender is None:
            raise RuntimeError("Sender is not initialized")

        timeout_ns = int(self.cfg.sample_timeout_sec * gst.SECOND)
        last_frame_time = time.monotonic()
        fps_window_start = last_frame_time
        fps_window_count = 0
        age_window = WindowMetric()
        appsink_wait_window = WindowMetric()
        copy_window = WindowMetric()
        send_window = WindowMetric()
        frame_proc_window = WindowMetric()
        pulled_window_count = 0
        stale_drop_window_count = 0

        logging.info("NDI sender '%s' is running (backend=gstreamer)", self.cfg.ndi_name)
        while not self.stop_event.is_set():
            self.check_bus()
            pull_start = time.monotonic()
            sample = self.appsink.emit("try-pull-sample", timeout_ns)
            pull_end = time.monotonic()
            appsink_wait_window.add((pull_end - pull_start) * 1000.0)
            if sample is None:
                if pull_end - last_frame_time > self.cfg.no_frame_restart_sec:
                    raise RuntimeError(
                        f"No video frames received for {self.cfg.no_frame_restart_sec:.1f}s"
                    )
                continue

            buffer = sample.get_buffer()
            if buffer is None:
                continue
            last_frame_time = pull_end

            mapped, map_info = buffer.map(gst.MapFlags.READ)
            if not mapped:
                continue

            try:
                frame_start = time.monotonic()
                mapped_size = getattr(map_info, "size", len(map_info.data))
                if mapped_size != self.expected_bytes:
                    logging.warning(
                        "Discarding frame with unexpected size: got %d bytes, expected %d",
                        mapped_size,
                        self.expected_bytes,
                    )
                    continue
                pulled_window_count += 1

                age_ms: float | None = None

                # Approximate local pipeline queueing delay from capture timestamp to send call.
                # PTS is in pipeline running-time domain.
                if self.pipeline is not None:
                    clock = self.pipeline.get_clock()
                    pts = buffer.pts
                    if clock is not None and pts is not None and pts != gst.CLOCK_TIME_NONE:
                        running_time = clock.get_time() - self.pipeline.get_base_time()
                        if running_time >= pts:
                            age_ms = (running_time - pts) / 1_000_000.0
                            age_window.add(age_ms)

                if (
                    self.cfg.drop_stale_ms > 0.0
                    and age_ms is not None
                    and age_ms > self.cfg.drop_stale_ms
                ):
                    stale_drop_window_count += 1
                    continue

                copy_start = time.monotonic()
                self.frame_buffer_view[:] = map_info.data
                copy_end = time.monotonic()
                copy_window.add((copy_end - copy_start) * 1000.0)

                send_start = time.monotonic()
                self.send_frame()
                send_end = time.monotonic()
                send_window.add((send_end - send_start) * 1000.0)
                frame_proc_window.add((send_end - frame_start) * 1000.0)
            finally:
                buffer.unmap(map_info)

            now = time.monotonic()
            last_frame_time = now
            fps_window_count += 1
            if now - fps_window_start >= 5.0:
                fps = fps_window_count / (now - fps_window_start)
                min_age_ms, avg_age_ms, max_age_ms = age_window.summary()
                if self.cfg.drop_stale_ms > 0.0:
                    stale_drop_ratio = 0.0
                    if pulled_window_count > 0:
                        stale_drop_ratio = (stale_drop_window_count / pulled_window_count) * 100.0
                    logging.info(
                        "Sending %.1f fps | capture->send age ms min=%.2f avg=%.2f max=%.2f | "
                        "step ms appsink_wait avg=%.2f map_copy avg=%.2f ndi_send avg=%.2f frame_proc avg=%.2f | "
                        "stale_drop=%d/%d (%.1f%%) threshold=%.1fms",
                        fps,
                        min_age_ms,
                        avg_age_ms,
                        max_age_ms,
                        appsink_wait_window.avg(),
                        copy_window.avg(),
                        send_window.avg(),
                        frame_proc_window.avg(),
                        stale_drop_window_count,
                        pulled_window_count,
                        stale_drop_ratio,
                        self.cfg.drop_stale_ms,
                    )
                else:
                    logging.info(
                        "Sending %.1f fps | capture->send age ms min=%.2f avg=%.2f max=%.2f | "
                        "step ms appsink_wait avg=%.2f map_copy avg=%.2f ndi_send avg=%.2f frame_proc avg=%.2f",
                        fps,
                        min_age_ms,
                        avg_age_ms,
                        max_age_ms,
                        appsink_wait_window.avg(),
                        copy_window.avg(),
                        send_window.avg(),
                        frame_proc_window.avg(),
                    )
                fps_window_start = now
                fps_window_count = 0
                age_window.reset()
                appsink_wait_window.reset()
                copy_window.reset()
                send_window.reset()
                frame_proc_window.reset()
                pulled_window_count = 0
                stale_drop_window_count = 0


class FFmpegHDMIToNDISender(BaseHDMIToNDISender):
    def __init__(self, cfg: SenderConfig, stop_event: Event) -> None:
        super().__init__(cfg, stop_event)
        self.proc: subprocess.Popen[bytes] | None = None
        self.read_offset = 0

    def build_command(self) -> list[str]:
        command = [
            self.cfg.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            self.cfg.ffmpeg_loglevel,
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-thread_queue_size",
            str(self.cfg.ffmpeg_thread_queue_size),
            "-f",
            "v4l2",
            "-input_format",
            self.cfg.ffmpeg_input_format,
            "-video_size",
            f"{self.cfg.width}x{self.cfg.height}",
            "-framerate",
            f"{self.cfg.fps_num}/{self.cfg.fps_den}",
            "-i",
            self.cfg.video_device,
            "-an",
            "-sn",
            "-vsync",
            str(self.cfg.ffmpeg_vsync),
            "-threads",
            str(self.cfg.ffmpeg_threads),
            "-pix_fmt",
            self.cfg.ffmpeg_pix_fmt,
            "-f",
            "rawvideo",
            "pipe:1",
        ]
        return command

    def start(self) -> None:
        expected_pix_fmt = FFMPEG_PIX_FMT_FOR_FOURCC.get(self.ndi_fourcc_name)
        if expected_pix_fmt is None:
            raise RuntimeError(
                f"ffmpeg backend does not currently support ndi_fourcc={self.ndi_fourcc_name}"
            )
        if self.cfg.ffmpeg_pix_fmt.strip().lower() != expected_pix_fmt:
            logging.warning(
                "ffmpeg_pix_fmt=%s does not match ndi_fourcc=%s; expected %s",
                self.cfg.ffmpeg_pix_fmt,
                self.ndi_fourcc_name,
                expected_pix_fmt,
            )
        self.start_ndi()
        command = self.build_command()
        logging.info("Starting capture process: %s", " ".join(command))
        self.proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        if self.proc.stdout is None:
            raise RuntimeError("Failed to open ffmpeg stdout pipe")

    def stop(self) -> None:
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=2)
            self.proc = None
        self.stop_ndi()

    def read_frame(self, timeout_sec: float) -> bool:
        if self.proc is None or self.proc.stdout is None:
            raise RuntimeError("ffmpeg process is not initialized")

        fd = self.proc.stdout.fileno()
        while self.read_offset < self.expected_bytes and not self.stop_event.is_set():
            ready, _, _ = select.select([fd], [], [], timeout_sec)
            if not ready:
                return False
            bytes_read = self.proc.stdout.readinto(self.frame_buffer_view[self.read_offset :])
            if bytes_read is None:
                continue
            if bytes_read == 0:
                rc = self.proc.poll()
                if rc is None:
                    return False
                raise RuntimeError(f"ffmpeg exited while reading frame (code={rc})")
            self.read_offset += bytes_read

        if self.read_offset < self.expected_bytes:
            return False
        self.read_offset = 0
        return True

    def run(self) -> None:
        if self.proc is None or self.video_frame is None or self.sender is None:
            raise RuntimeError("Sender is not initialized")

        last_frame_time = time.monotonic()
        fps_window_start = last_frame_time
        fps_window_count = 0
        read_window = WindowMetric()
        send_window = WindowMetric()
        frame_proc_window = WindowMetric()

        logging.info("NDI sender '%s' is running (backend=ffmpeg)", self.cfg.ndi_name)
        while not self.stop_event.is_set():
            if self.proc.poll() is not None:
                raise RuntimeError(f"ffmpeg exited unexpectedly (code={self.proc.returncode})")

            read_start = time.monotonic()
            got_frame = self.read_frame(self.cfg.sample_timeout_sec)
            read_end = time.monotonic()
            if not got_frame:
                if read_end - last_frame_time > self.cfg.no_frame_restart_sec:
                    raise RuntimeError(
                        f"No video frames received for {self.cfg.no_frame_restart_sec:.1f}s"
                    )
                continue

            read_window.add((read_end - read_start) * 1000.0)
            send_start = time.monotonic()
            self.send_frame()
            send_end = time.monotonic()
            send_window.add((send_end - send_start) * 1000.0)
            frame_proc_window.add((send_end - read_start) * 1000.0)

            last_frame_time = send_end
            fps_window_count += 1
            if send_end - fps_window_start >= 5.0:
                fps = fps_window_count / (send_end - fps_window_start)
                logging.info(
                    "Sending %.1f fps | step ms frame_read avg=%.2f ndi_send avg=%.2f frame_proc avg=%.2f",
                    fps,
                    read_window.avg(),
                    send_window.avg(),
                    frame_proc_window.avg(),
                )
                fps_window_start = send_end
                fps_window_count = 0
                read_window.reset()
                send_window.reset()
                frame_proc_window.reset()


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

    backend = cfg.capture_backend.strip().lower()
    if backend in {"ffmpeg"}:
        sender: BaseHDMIToNDISender = FFmpegHDMIToNDISender(cfg, stop_event)
    elif backend in {"gst", "gstreamer"}:
        sender = GStreamerHDMIToNDISender(cfg, stop_event)
    else:
        logging.error("Unsupported capture_backend '%s'", cfg.capture_backend)
        return 2

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
