"""Pull a time range out of the rolling segment buffer."""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from hype_detector import SpikeEvent
from media import run_ffmpeg
from segments import Segment, SegmentIndex

log = logging.getLogger(__name__)

GAP_WARN_SECONDS = 1.0   # warn when buffered segments are not contiguous
WAIT_POLL_SECONDS = 0.5


class CutError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClipJob:
    number: int
    spike: SpikeEvent
    start: float       # wall clock
    end: float         # wall clock
    channel: str

    @property
    def tag(self) -> str:
        return self.spike.tag


@dataclass(frozen=True)
class CutResult:
    source: Path       # stream-copied concatenation of the needed segments
    offset: float      # where the clip starts inside `source`
    duration: float


def select_segments(segments: list[Segment], start: float, end: float) -> tuple[list[Segment], float, float]:
    """Pick segments covering [start, end]. Returns (segments, offset_in_first, duration)."""
    chosen = sorted((s for s in segments if s.wall_end > start and s.wall_start < end),
                    key=lambda s: s.wall_start)
    if not chosen:
        raise CutError(f"no buffered video covers {start:.1f}–{end:.1f}")
    for a, b in zip(chosen, chosen[1:]):
        if b.wall_start - a.wall_end > GAP_WARN_SECONDS:
            log.warning("gap of %.1fs in buffered video (reconnect?) — clip will skip it",
                        b.wall_start - a.wall_end)
    clip_start = max(start, chosen[0].wall_start)
    clip_end = min(end, chosen[-1].wall_end)
    return chosen, clip_start - chosen[0].wall_start, clip_end - clip_start


def wait_for_coverage(index: SegmentIndex, end: float, timeout: float) -> None:
    """Block until the buffer reaches `end`, recording stops, or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        latest = index.latest_end()
        if (latest is not None and latest >= end) or index.closed.is_set():
            return
        time.sleep(WAIT_POLL_SECONDS)
    log.warning("timed out waiting for post-roll; cutting what is buffered")


def cut(job: ClipJob, index: SegmentIndex, workdir: Path, wait_timeout: float) -> CutResult:
    wait_for_coverage(index, job.end, wait_timeout)
    segs, offset, duration = select_segments(index.snapshot(), job.start, job.end)
    workdir.mkdir(parents=True, exist_ok=True)

    # Copy out first so buffer rotation can't delete files mid-edit.
    names: list[str] = []
    for seg in segs:
        try:
            shutil.copy2(seg.path, workdir / seg.path.name)
            names.append(seg.path.name)
        except FileNotFoundError:
            log.warning("segment %s vanished before it could be copied", seg.path.name)
    if not names:
        raise CutError("all needed segments were rotated out")

    concat = workdir / "concat.txt"
    concat.write_text("".join(f"file '{n}'\n" for n in names), encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", concat.name, "-map", "0", "-c", "copy", "joined.ts"],
               cwd=workdir)
    for n in names:
        (workdir / n).unlink(missing_ok=True)
    return CutResult(source=workdir / "joined.ts", offset=offset, duration=duration)


def save_raw(result: CutResult, out_path: Path) -> None:
    """Fallback output: keyframe-accurate stream copy, no editing."""
    run_ffmpeg(["-ss", f"{result.offset:.3f}", "-t", f"{result.duration:.3f}", "-i", str(result.source),
                "-c", "copy", "-movflags", "+faststart", str(out_path)])
