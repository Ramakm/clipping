"""Thin helpers around the ffmpeg / ffprobe binaries."""

from __future__ import annotations

import functools
import json
import logging
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    pass


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise FFmpegError(f"'{name}' not found on PATH — see README for install steps")
    return path


def run_ffmpeg(args: list[str], cwd: Path | None = None) -> None:
    """Run ffmpeg with an argument list. Logs the full command on failure."""
    cmd = [require_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("ffmpeg failed (exit %d): %s\n%s", proc.returncode, shlex.join(cmd), proc.stderr.strip())
        raise FFmpegError(proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "ffmpeg failed")


@functools.cache
def has_filter(name: str) -> bool:
    """True if this ffmpeg build includes filter `name` (e.g. 'ass' needs libass)."""
    proc = subprocess.run([require_binary("ffmpeg"), "-hide_banner", "-filters"], capture_output=True, text=True)
    return any(line.split()[1:2] == [name] for line in proc.stdout.splitlines() if line.strip())


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    has_audio: bool
    duration: float


def probe(path: Path) -> MediaInfo:
    cmd = [require_binary("ffprobe"), "-v", "error", "-print_format", "json",
           "-show_streams", "-show_format", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("ffprobe failed: %s\n%s", shlex.join(cmd), proc.stderr.strip())
        raise FFmpegError(f"ffprobe failed on {path}")
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise FFmpegError(f"no video stream in {path}")
    return MediaInfo(
        width=int(video["width"]),
        height=int(video["height"]),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        duration=float(data.get("format", {}).get("duration", 0) or 0),
    )


def segment_command(input_arg: str, out_dir: Path, segment_seconds: int, start_number: int,
                    list_name: str) -> list[str]:
    """ffmpeg args that split `input_arg` into numbered .ts chunks + a CSV segment list."""
    return [
        "-i", input_arg,
        "-map", "0:v:0?", "-map", "0:a:0?",
        "-c", "copy",
        "-f", "segment",
        "-segment_time", str(segment_seconds),
        "-reset_timestamps", "1",
        "-segment_start_number", str(start_number),
        "-segment_list", str(out_dir / list_name),
        "-segment_list_type", "csv",
        str(out_dir / "seg_%06d.ts"),
    ]
