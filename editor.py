"""Vertical 9:16 render: facecam-over-gameplay (or blurred background), captions, loudnorm."""

from __future__ import annotations

import logging
from pathlib import Path

from config import EditorConfig, FacecamBox
from cutter import CutResult
from media import has_filter, probe, run_ffmpeg

log = logging.getLogger(__name__)


def _even(n: float) -> int:
    return int(round(n / 2)) * 2


def facecam_height(cfg: EditorConfig) -> int:
    return _even(cfg.height * cfg.facecam_height_ratio)


def is_landscape(cfg: EditorConfig) -> bool:
    return cfg.format == "landscape"


def caption_y(cfg: EditorConfig, facecam: FacecamBox | None) -> int:
    """Captions sit on the facecam/gameplay seam, lower-middle without facecam, near the bottom in landscape."""
    if is_landscape(cfg):
        return int(cfg.height * cfg.caption_y_landscape)
    return facecam_height(cfg) if facecam else int(cfg.height * cfg.caption_y_no_facecam)


def valid_facecam(box: FacecamBox | None, src_w: int, src_h: int) -> FacecamBox | None:
    if box is None:
        return None
    if box.w <= 0 or box.h <= 0 or box.x < 0 or box.y < 0 or box.x + box.w > src_w or box.y + box.h > src_h:
        log.warning("facecam box %s is outside the %dx%d source — using blur layout", box, src_w, src_h)
        return None
    return box


def build_filtergraph(cfg: EditorConfig, facecam: FacecamBox | None, ass_name: str | None,
                      has_audio: bool) -> str:
    W, H = cfg.width, cfg.height
    if is_landscape(cfg):
        # keep the whole frame; letterbox if the source isn't 16:9
        video = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                 f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1")
    elif facecam:
        top = facecam_height(cfg)
        bottom = H - top
        video = (
            f"[0:v]split=2[c][g];"
            f"[c]crop={facecam.w}:{facecam.h}:{facecam.x}:{facecam.y},"
            f"scale={W}:{top}:force_original_aspect_ratio=increase,crop={W}:{top},setsar=1[cam];"
            f"[g]scale={W}:{bottom}:force_original_aspect_ratio=increase,crop={W}:{bottom},setsar=1[game];"
            f"[cam][game]vstack=inputs=2"
        )
    else:
        video = (
            f"[0:v]split=2[b][f];"
            f"[b]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"boxblur={cfg.blur_strength}:2[bg];"
            f"[f]scale={W}:-2,setsar=1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )
    video += f",fps={cfg.fps},format=yuv420p"
    if ass_name:
        video += f",ass={ass_name}"
    graph = video + "[v]"
    if has_audio:
        graph += f";[0:a]{cfg.loudnorm},aresample=48000[a]"
    return graph


def render(cut: CutResult, out_path: Path, cfg: EditorConfig, facecam: FacecamBox | None,
           ass_path: Path | None) -> None:
    """Encode the final short. `ass_path` must live in the cut's working directory."""
    info = probe(cut.source)
    facecam = None if is_landscape(cfg) else valid_facecam(facecam, info.width, info.height)
    workdir = cut.source.parent
    if ass_path and not has_filter("ass"):
        log.warning("this ffmpeg has no libass ('ass' filter) — captions not burned in; "
                    "install a full ffmpeg build (see README)")
        ass_path = None
    ass_name = ass_path.name if ass_path else None  # relative name avoids filter-path escaping
    graph = build_filtergraph(cfg, facecam, ass_name, info.has_audio)
    args = ["-ss", f"{cut.offset:.3f}", "-t", f"{cut.duration:.3f}", "-i", cut.source.name,
            "-filter_complex", graph, "-map", "[v]"]
    if info.has_audio:
        args += ["-map", "[a]", "-c:a", "aac", "-b:a", cfg.audio_bitrate, "-ar", "48000"]
    args += ["-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf), "-r", str(cfg.fps),
             "-t", f"{cut.duration:.3f}", "-movflags", "+faststart", str(out_path.resolve())]
    run_ffmpeg(args, cwd=workdir)
