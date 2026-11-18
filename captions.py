"""faster-whisper transcription → word-level karaoke-style ASS subtitles."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import CaptionsConfig
from media import run_ffmpeg

log = logging.getLogger(__name__)

WHISPER_SAMPLE_RATE = 16000
PCM_SCALE = 32768.0  # int16 → [-1, 1)

_model_lock = threading.Lock()
_model: Any = None


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


def detect_device(pref: str) -> tuple[str, str]:
    """Return (device, compute_type)."""
    if pref == "cpu":
        return "cpu", "int8"
    if pref == "cuda":
        return "cuda", "float16"
    try:
        import ctranslate2  # type: ignore[import-untyped]
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def _get_model(cfg: CaptionsConfig) -> Any:
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]
            device, compute = detect_device(cfg.device)
            log.info("loading whisper '%s' on %s (%s)", cfg.model, device, compute)
            _model = WhisperModel(cfg.model, device=device, compute_type=compute)
        return _model


def transcribe(media: Path, offset: float, duration: float, workdir: Path,
               cfg: CaptionsConfig) -> list[Word]:
    import numpy as np

    # Decode with ffmpeg to raw 16 kHz mono PCM and hand whisper a float array. This avoids
    # faster-whisper's PyAV decoder, whose API breaks between PyAV releases.
    pcm = workdir / "audio.pcm"
    run_ffmpeg(["-ss", f"{offset:.3f}", "-t", f"{duration:.3f}", "-i", str(media),
                "-vn", "-ac", "1", "-ar", str(WHISPER_SAMPLE_RATE), "-f", "s16le", str(pcm)])
    audio = np.fromfile(pcm, dtype=np.int16).astype(np.float32) / PCM_SCALE
    pcm.unlink(missing_ok=True)
    if audio.size == 0:
        return []
    model = _get_model(cfg)
    language = None if cfg.language == "auto" else cfg.language
    segments, _ = model.transcribe(audio, language=language, word_timestamps=True, vad_filter=True)
    return [Word(w.start, w.end, w.word.strip())
            for seg in segments for w in (seg.words or []) if w.word.strip()]


def group_words(words: list[Word], max_words: int, max_gap: float) -> list[list[Word]]:
    groups: list[list[Word]] = []
    for w in words:
        cur = groups[-1] if groups else None
        if (cur is None or len(cur) >= max_words or w.start - cur[-1].end > max_gap
                or cur[-1].text[-1:] in ".!?"):
            groups.append([w])
        else:
            cur.append(w)
    return groups


def _ass_time(t: float) -> str:
    cs = max(int(round(t * 100)), 0)
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str, upper: bool) -> str:
    text = text.replace("\\", "").replace("{", "").replace("}", "")
    return text.upper() if upper else text


def words_to_ass(words: list[Word], cfg: CaptionsConfig, width: int, height: int, y: int) -> str:
    """Build an ASS script: up to `max_words` words on screen, active word highlighted."""
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{cfg.font},{cfg.font_size},{cfg.primary_color},{cfg.primary_color},"
        f"{cfg.outline_color},{cfg.outline_color},-1,0,0,0,100,100,0,0,1,{cfg.outline},0,5,40,40,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events: list[str] = []
    pos = f"{{\\an5\\pos({width // 2},{y})}}"
    for group in group_words(words, cfg.max_words, cfg.max_gap):
        texts = [_clean(w.text, cfg.uppercase) for w in group]
        for i, w in enumerate(group):
            end = group[i + 1].start if i + 1 < len(group) else w.end
            parts = [f"{{\\c{cfg.highlight_color}}}{t}{{\\c{cfg.primary_color}}}" if j == i else t
                     for j, t in enumerate(texts)]
            events.append(f"Dialogue: 0,{_ass_time(w.start)},{_ass_time(max(end, w.start + 0.05))},"
                          f"Default,,0,0,0,,{pos}{' '.join(parts)}")
    return header + "\n".join(events) + "\n"


def transcript_text(words: list[Word]) -> str:
    return " ".join(w.text for w in words).strip()
