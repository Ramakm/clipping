"""Clip worker: ClipJob → cut → captions → vertical edit → mp4 + json."""

from __future__ import annotations

import json
import logging
import queue
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

import captions
import editor
from config import AppConfig, FacecamBox
from cutter import ClipJob, CutResult, cut, save_raw
from media import has_filter
from segments import SegmentIndex

log = logging.getLogger(__name__)

TITLE_MAX_CHARS = 70
_SENTINEL = None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "clip"


def next_clip_number(clips_dir: Path) -> int:
    nums = [int(m.group(1)) for p in clips_dir.glob("clip_*.mp4") if (m := re.match(r"clip_(\d+)_", p.name))]
    return max(nums, default=0) + 1


def suggest_title(channel: str, transcript: str, top_messages: tuple[str, ...]) -> str:
    """v1 heuristic: first spoken sentence if short enough, else the top chat reaction."""
    hook = ""
    if transcript:
        first = re.split(r"(?<=[.!?])\s+", transcript.strip())[0]
        if 2 <= len(first.split()) <= 12:
            hook = first.rstrip(".")
    if not hook and top_messages:
        hook = top_messages[0].upper()
    if not hook:
        hook = "This moment"
    title = f"{hook} | {channel}"
    return title if len(title) <= TITLE_MAX_CHARS else title[: TITLE_MAX_CHARS - 1] + "…"


class ClipWorker(threading.Thread):
    """Processes ClipJobs one at a time so detection never blocks on encoding."""

    def __init__(self, cfg: AppConfig, index: SegmentIndex, facecam: FacecamBox | None,
                 clock_label: "callable[[float], str]") -> None:  # type: ignore[valid-type]
        super().__init__(name="clip-worker", daemon=True)
        self.cfg = cfg
        self.index = index
        self.facecam = facecam
        self.clock_label = clock_label
        self.jobs: "queue.Queue[ClipJob | None]" = queue.Queue()
        self.done = 0
        self.failed = 0
        self.busy = False

    def submit(self, job: ClipJob) -> None:
        self.jobs.put(job)

    def finish(self) -> None:
        """Process everything queued so far, then exit."""
        self.jobs.put(_SENTINEL)

    def run(self) -> None:
        while (job := self.jobs.get()) is not _SENTINEL:
            self.busy = True
            try:
                self.process(job)
                self.done += 1
            except Exception:
                self.failed += 1
                log.exception("clip %d failed", job.number)
            finally:
                self.busy = False

    def process(self, job: ClipJob) -> None:
        clips_dir = self.cfg.clips_dir
        clips_dir.mkdir(parents=True, exist_ok=True)
        stem = f"clip_{job.number:03d}_{slug(job.tag)}"
        workdir = self.cfg.buffer_dir / f"work_{stem}"
        out_mp4 = clips_dir / f"{stem}.mp4"
        log.info("✂  cutting %s (score %.1f)", stem, job.spike.score)
        try:
            result = cut(job, self.index, workdir, self.cfg.recorder.segment_wait_timeout)
            words = self._captions(result, workdir)
            ass = workdir / "captions.ass" if words else None
            if ass is not None:
                y = editor.caption_y(self.cfg.editor, self.facecam)
                ass.write_text(captions.words_to_ass(words, self.cfg.captions, self.cfg.editor.width,
                                                     self.cfg.editor.height, y), encoding="utf-8")
            try:
                editor.render(result, out_mp4, self.cfg.editor, self.facecam, ass)
            except Exception:
                log.exception("edit failed for %s — saving raw clip instead", stem)
                save_raw(result, out_mp4)
            if ass is not None and not has_filter("ass"):
                shutil.copy2(ass, clips_dir / f"{stem}.ass")  # sidecar so captions aren't lost
            transcript = captions.transcript_text(words)
            self._write_meta(clips_dir / f"{stem}.json", job, result, transcript)
            log.info("✅ saved %s", out_mp4)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _captions(self, result: CutResult, workdir: Path) -> list[captions.Word]:
        if not self.cfg.captions.enabled:
            return []
        try:
            return captions.transcribe(result.source, result.offset, result.duration, workdir, self.cfg.captions)
        except Exception:
            log.exception("captioning failed — continuing without captions")
            return []

    def _write_meta(self, path: Path, job: ClipJob, result: CutResult, transcript: str) -> None:
        if self.cfg.llm.enabled:
            log.warning("llm.enabled is set but LLM titles are not part of v1 — using heuristic title")
        meta = {
            "channel": job.channel,
            "spike_time": self.clock_label(job.spike.start),
            "score": job.spike.score,
            "duration": round(result.duration, 2),
            "top_messages": list(job.spike.top_messages),
            "transcript": transcript,
            "suggested_title": suggest_title(job.channel, transcript, job.spike.top_messages),
            "detector": {
                "rate": job.spike.rate,
                "baseline_mean": job.spike.baseline_mean,
                "baseline_std": job.spike.baseline_std,
                "hype_ratio": job.spike.hype_ratio,
                "tag": job.spike.tag,
            },
        }
        path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def wall_label(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def offset_label(ts: float) -> str:
    m, s = divmod(int(ts), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
