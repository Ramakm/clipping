"""Shared, thread-safe index of buffered video segments on the wall clock."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Segment:
    path: Path
    wall_start: float   # same clock as chat timestamps
    duration: float

    @property
    def wall_end(self) -> float:
        return self.wall_start + self.duration


def parse_segment_list_line(line: str) -> tuple[str, float, float] | None:
    """Parse one ffmpeg CSV segment-list line: 'name,start,end'."""
    parts = line.strip().split(",")
    if len(parts) < 3:
        return None
    try:
        return parts[0], float(parts[1]), float(parts[2])
    except ValueError:
        return None


class SegmentIndex:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._segments: list[Segment] = []
        self.closed = threading.Event()   # set when no more segments will arrive

    def add(self, seg: Segment) -> None:
        with self._lock:
            self._segments.append(seg)
            self._segments.sort(key=lambda s: s.wall_start)

    def snapshot(self) -> list[Segment]:
        with self._lock:
            return list(self._segments)

    def latest_end(self) -> float | None:
        with self._lock:
            return self._segments[-1].wall_end if self._segments else None

    def pop_older_than(self, cutoff: float) -> list[Segment]:
        with self._lock:
            old = [s for s in self._segments if s.wall_end < cutoff]
            self._segments = [s for s in self._segments if s.wall_end >= cutoff]
            return old

    def __len__(self) -> int:
        with self._lock:
            return len(self._segments)
