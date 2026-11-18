"""Live Stream Auto-Clipper — entry point.

  python clipper.py https://twitch.tv/<channel>
  python clipper.py https://youtube.com/watch?v=<live_id>
  python clipper.py --file sample.mp4 --chat sample_chat.json     # offline replay
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import queue
import sys
import threading
import time
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.logging import RichHandler
from rich.text import Text

import editor
from chat import ChatMessage, create_chat_source, parse_stream_url
from chat.replay import load_chat_file
from config import AppConfig, ConfigError, FacecamBox, load_config
from cutter import ClipJob
from hype_detector import HypeDetector, SpikeEvent
from media import FFmpegError, probe, require_binary, run_ffmpeg, segment_command
from pipeline import ClipWorker, next_clip_number, offset_label, wall_label
from recorder import Recorder, clean_buffer
from segments import Segment, SegmentIndex, parse_segment_list_line

log = logging.getLogger("clipper")
console = Console()

TICK_SECONDS = 1.0
STATUS_REFRESH_PER_SECOND = 2
FACECAM_WAIT_SECONDS = 60


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Auto-clip hype moments from a live stream into vertical shorts.")
    p.add_argument("url", nargs="?", help="twitch.tv/<channel> or YouTube live URL")
    p.add_argument("--file", type=Path, help="offline replay: a recorded video file")
    p.add_argument("--chat", type=Path, help="offline replay: chat log JSON (see chat/replay.py)")
    p.add_argument("--channel", help="channel name for allowlist/facecam lookup (overrides URL)")
    p.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    p.add_argument("--detect-facecam", action="store_true", help="suggest a facecam box with MediaPipe")
    p.add_argument("--warmup", type=float, help="override detector.warmup_seconds")
    p.add_argument("--no-captions", action="store_true", help="skip whisper captions")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    if bool(args.url) == bool(args.file):
        p.error("give either a stream URL or --file (offline replay)")
    if args.file and not args.chat:
        p.error("--file needs --chat")
    return args


def apply_overrides(cfg: AppConfig, args: argparse.Namespace) -> AppConfig:
    if args.warmup is not None:
        cfg = dataclasses.replace(cfg, detector=dataclasses.replace(cfg.detector, warmup_seconds=args.warmup))
    if args.no_captions:
        cfg = dataclasses.replace(cfg, captions=dataclasses.replace(cfg.captions, enabled=False))
    return cfg


def check_allowlist(cfg: AppConfig, channel: str) -> None:
    if cfg.channel(channel) is None:
        log.warning("⚠  channel '%s' is not in the config allowlist — only clip streamers who allow it", channel)


def make_job(cfg: AppConfig, spike: SpikeEvent, number: int, channel: str) -> ClipJob:
    return ClipJob(number=number, spike=spike, start=spike.start - cfg.clip.pre_roll,
                   end=spike.start + cfg.clip.post_roll, channel=channel)


def resolve_facecam(cfg: AppConfig, channel: str, args: argparse.Namespace,
                    sample: Path | None) -> FacecamBox | None:
    ch = cfg.channel(channel)
    box = ch.facecam if ch else None
    if not args.detect_facecam:
        return box
    if sample is None:
        log.warning("no video available yet for facecam detection")
        return box
    from facecam import detect_facecam
    info = probe(sample)
    aspect = cfg.editor.width / editor.facecam_height(cfg.editor)
    found = detect_facecam(sample, cfg.buffer_dir, info.width, info.height, aspect, cfg.facecam_detect.expand)
    if found:
        log.info("suggested facecam box — add under channels.%s in config.yaml:\n"
                 "    facecam: { x: %d, y: %d, w: %d, h: %d }", channel, found.x, found.y, found.w, found.h)
        return found
    return box


# ---------------------------------------------------------------- live mode

def run_live(cfg: AppConfig, args: argparse.Namespace) -> int:
    target = parse_stream_url(args.url)
    channel = (args.channel or target.channel).lower()
    check_allowlist(cfg, channel)
    clean_buffer(cfg.buffer_dir)

    stop = threading.Event()
    index = SegmentIndex()
    chat_q: "queue.Queue[ChatMessage]" = queue.Queue()
    recorder = Recorder(target.url, cfg, index, stop)
    chat_src = create_chat_source(target)
    chat_thread = threading.Thread(target=chat_src.pump, args=(chat_q, stop), name="chat", daemon=True)
    detector = HypeDetector(cfg.detector, cfg.clip.cooldown)

    recorder.start()
    chat_thread.start()
    log.info("watching %s (%s) — Ctrl+C to stop", channel, target.platform)

    facecam = resolve_facecam(cfg, channel, args, _first_segment(index, recorder) if args.detect_facecam else None)
    worker = ClipWorker(cfg, index, facecam, wall_label)
    worker.start()
    number = next_clip_number(cfg.clips_dir)
    interrupted = False
    chat_delay = cfg.chat_delay.get(target.platform, 0.0)

    def status() -> Text:
        live = "[green]● LIVE[/]" if len(index) else "[yellow]● CONNECTING[/]"
        busy = "  [cyan]editing…[/]" if worker.busy else ""
        return Text.from_markup(f"{live}  chat {detector.rate:.0f} msg/s  baseline {detector.baseline_mean:.0f}"
                                f"  clips {worker.done}{busy}")

    try:
        with Live(status(), console=console, refresh_per_second=STATUS_REFRESH_PER_SECOND,
                  get_renderable=status, transient=True):
            while not recorder.ended.is_set():
                _drain(chat_q, detector)
                spike = detector.tick(time.time() - chat_delay)
                if spike:
                    log.info("🔥 spike: %.1f msg/s (baseline %.1f) score %.1f — %s",
                             spike.rate, spike.baseline_mean, spike.score, ", ".join(spike.top_messages[:3]))
                    worker.submit(make_job(cfg, spike, number, channel))
                    number += 1
                time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        interrupted = True
        log.info("stopping — finishing in-progress clips…")
    finally:
        stop.set()
        recorder.join()
        worker.finish()
        try:
            worker.join()
        except KeyboardInterrupt:
            log.warning("forced exit — in-progress clip abandoned")
        clean_buffer(cfg.buffer_dir)

    if recorder.error and not interrupted:
        log.error(recorder.error)
        return 1
    log.info("done — %d clip(s) saved to %s", worker.done, cfg.clips_dir)
    return 0


def _drain(q: "queue.Queue[ChatMessage]", detector: HypeDetector) -> None:
    while True:
        try:
            detector.add(q.get_nowait())
        except queue.Empty:
            return


def _first_segment(index: SegmentIndex, recorder: Recorder) -> Path | None:
    deadline = time.monotonic() + FACECAM_WAIT_SECONDS
    while time.monotonic() < deadline and not recorder.ended.is_set():
        segs = index.snapshot()
        if segs:
            return segs[0].path
        time.sleep(TICK_SECONDS)
    return None


# ---------------------------------------------------------------- replay mode

def run_replay(cfg: AppConfig, args: argparse.Namespace) -> int:
    """Replay a recorded video + chat log faster than real time. Clock = seconds into the video."""
    video: Path = args.file
    if not video.exists():
        log.error("file not found: %s", video)
        return 1
    channel = (args.channel or video.stem).lower()
    check_allowlist(cfg, channel)
    clean_buffer(cfg.buffer_dir)
    cfg.buffer_dir.mkdir(parents=True, exist_ok=True)

    log.info("segmenting %s …", video.name)
    index = build_replay_index(video, cfg)
    index.closed.set()
    messages = load_chat_file(args.chat)
    log.info("%d segments, %d chat messages", len(index), len(messages))

    facecam = resolve_facecam(cfg, channel, args, video)
    worker = ClipWorker(cfg, index, facecam, offset_label)
    worker.start()
    detector = HypeDetector(cfg.detector, cfg.clip.cooldown)
    number = next_clip_number(cfg.clips_dir)
    end = index.latest_end() or 0.0

    i, t = 0, 0.0
    try:
        while t <= end:
            while i < len(messages) and messages[i].ts <= t:
                detector.add(messages[i])
                i += 1
            if spike := detector.tick(t):
                log.info("🔥 spike at %s: %.1f msg/s (baseline %.1f) score %.1f — %s", offset_label(spike.start),
                         spike.rate, spike.baseline_mean, spike.score, ", ".join(spike.top_messages[:3]))
                worker.submit(make_job(cfg, spike, number, channel))
                number += 1
            t += TICK_SECONDS
        worker.finish()
        worker.join()
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        clean_buffer(cfg.buffer_dir)
    log.info("replay done — %d clip(s) saved, %d failed → %s", worker.done, worker.failed, cfg.clips_dir)
    return 0 if worker.failed == 0 else 1


def build_replay_index(video: Path, cfg: AppConfig) -> SegmentIndex:
    list_name = "replay.csv"
    run_ffmpeg(segment_command(str(video.resolve()), cfg.buffer_dir, cfg.recorder.segment_seconds, 0, list_name))
    index = SegmentIndex()
    origin: float | None = None
    for line in (cfg.buffer_dir / list_name).read_text(encoding="utf-8").splitlines():
        parsed = parse_segment_list_line(line)
        if parsed is None:
            continue
        name, start, stop = parsed
        origin = start if origin is None else origin
        index.add(Segment(cfg.buffer_dir / name, start - origin, stop - start))
    return index


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s",
                        datefmt="[%X]", handlers=[RichHandler(console=console, show_path=False, markup=False)])
    if not args.verbose:
        logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    try:
        cfg = apply_overrides(load_config(args.config), args)
        require_binary("ffmpeg")
        require_binary("ffprobe")
        return run_replay(cfg, args) if args.file else run_live(cfg, args)
    except (ConfigError, FFmpegError, ValueError) as e:
        log.error(str(e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
