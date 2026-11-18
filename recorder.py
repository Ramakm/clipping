"""Live stream → rolling buffer of short .ts segments.

streamlink (or yt-dlp) pipes the stream into ffmpeg's segment muxer. Finished
segments are read from ffmpeg's CSV segment list and mapped onto the wall clock.
"""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from config import AppConfig
from media import require_binary, segment_command
from segments import Segment, SegmentIndex, parse_segment_list_line

log = logging.getLogger(__name__)

PROCESS_STOP_TIMEOUT = 10


def find_tool(name: str) -> str | None:
    """PATH lookup, falling back to the running interpreter's bin/Scripts dir (un-activated venv)."""
    found = shutil.which(name) or shutil.which(name, path=str(Path(sys.executable).parent))
    return found


def source_commands(url: str) -> list[list[str]]:
    """Stream readers to try in order; each writes the stream to stdout."""
    cmds: list[list[str]] = []
    if sl := find_tool("streamlink"):
        cmds.append([sl, "--loglevel", "error", "--stdout", url, "best"])
    if yd := find_tool("yt-dlp"):
        cmds.append([yd, "--quiet", "--no-warnings", "-f", "best", "--hls-use-mpegts", "-o", "-", url])
    return cmds


class WallClockMapper:
    """Maps stream-timeline seconds to wall-clock seconds for one ffmpeg run.

    Anchored when the first segment completes, then follows the stream timeline
    (smooth, immune to download bursts). Re-anchors if drift exceeds tolerance.
    """

    def __init__(self, tolerance: float) -> None:
        self.tolerance = tolerance
        self.offset: float | None = None

    def to_wall(self, stream_start: float, stream_end: float, now: float) -> float:
        if self.offset is None or abs(now - (self.offset + stream_end)) > self.tolerance:
            if self.offset is not None:
                log.info("video clock drifted; re-anchoring")
            self.offset = now - stream_end
        return self.offset + stream_start


class Recorder(threading.Thread):
    def __init__(self, url: str, cfg: AppConfig, index: SegmentIndex, stop: threading.Event) -> None:
        super().__init__(name="recorder", daemon=True)
        self.url = url
        self.cfg = cfg
        self.rc = cfg.recorder
        self.index = index
        self.stop_event = stop
        self.buffer_dir = cfg.buffer_dir
        self.ended = threading.Event()
        self.error: str | None = None
        self._next_number = 0
        self._run = 0

    # ---- thread entry ----------------------------------------------------
    def run(self) -> None:
        try:
            self._record_loop()
        except Exception as e:
            self.error = str(e)
            log.exception("recorder crashed")
        finally:
            self.index.closed.set()
            self.ended.set()

    def _record_loop(self) -> None:
        require_binary("ffmpeg")
        sources = source_commands(self.url)
        if not sources:
            raise RuntimeError("neither streamlink nor yt-dlp is installed")
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        ever_recorded = False
        failures = 0
        while not self.stop_event.is_set():
            got = any(self._record_once(cmd) for cmd in sources if not self.stop_event.is_set())
            if got:
                ever_recorded, failures = True, 0
                if not self.stop_event.is_set():
                    log.warning("stream dropped — reconnecting")
                continue
            failures += 1
            if not ever_recorded:
                self.error = "could not open the stream (offline, wrong URL, or geo-blocked?)"
                return
            if failures >= self.rc.reconnect_attempts:
                log.info("stream appears to have ended")
                return
            log.warning("reconnect attempt %d/%d failed", failures, self.rc.reconnect_attempts)
            self.stop_event.wait(self.rc.reconnect_delay)

    # ---- one connection -------------------------------------------------
    def _record_once(self, src_cmd: list[str]) -> bool:
        """Run source | ffmpeg until it exits. Returns True if any segment was produced."""
        self._run += 1
        list_name = f"run_{self._run:03d}.csv"
        list_path = self.buffer_dir / list_name
        ff_cmd = [require_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                  *segment_command("pipe:0", self.buffer_dir, self.rc.segment_seconds,
                                   self._next_number, list_name)]
        log.info("connecting via %s", Path(src_cmd[0]).name)
        log.debug("source: %s | ffmpeg: %s", shlex.join(src_cmd), shlex.join(ff_cmd))
        src = subprocess.Popen(src_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        ff = subprocess.Popen(ff_cmd, stdin=src.stdout, stderr=subprocess.PIPE)
        assert src.stdout is not None
        src.stdout.close()  # ffmpeg owns the read end now

        mapper = WallClockMapper(self.rc.drift_tolerance)
        consumed = 0
        produced = 0
        stopping = False
        while True:
            exited = ff.poll() is not None
            consumed, n = self._consume_list(list_path, consumed, mapper)
            produced += n
            self._rotate()
            if exited:
                break
            if self.stop_event.is_set() and not stopping:
                stopping = True
                src.terminate()  # ffmpeg sees EOF and finalises the last segment
            time.sleep(self.rc.poll_interval)

        _reap(src)
        if ff.returncode not in (0, None) and produced == 0:
            err = (ff.stderr.read().decode(errors="replace").strip() if ff.stderr else "")
            src_err = (src.stderr.read().decode(errors="replace").strip() if src.stderr else "")
            log.warning("%s produced no video. %s %s", src_cmd[0], src_err[-300:], err[-300:])
        list_path.unlink(missing_ok=True)
        return produced > 0

    def _consume_list(self, list_path: Path, consumed: int, mapper: WallClockMapper) -> tuple[int, int]:
        try:
            text = list_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return consumed, 0
        lines = text.split("\n")[:-1]  # only complete lines
        added = 0
        now = time.time()
        for line in lines[consumed:]:
            parsed = parse_segment_list_line(line)
            if parsed is None:
                continue
            name, start, end = parsed
            wall_start = mapper.to_wall(start, end, now)
            self.index.add(Segment(self.buffer_dir / name, wall_start, end - start))
            self._next_number = max(self._next_number, _segment_number(name) + 1)
            added += 1
        return len(lines), added

    def _rotate(self) -> None:
        cutoff = time.time() - self.cfg.buffer_minutes * 60
        for seg in self.index.pop_older_than(cutoff):
            seg.path.unlink(missing_ok=True)


def _segment_number(name: str) -> int:
    try:
        return int(Path(name).stem.split("_")[-1])
    except ValueError:
        return 0


def _reap(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(PROCESS_STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()


def clean_buffer(buffer_dir: Path) -> None:
    if not buffer_dir.exists():
        return
    for p in buffer_dir.iterdir():
        if p.suffix in {".ts", ".csv"}:
            p.unlink(missing_ok=True)
