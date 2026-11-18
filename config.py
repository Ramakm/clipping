"""Typed view over config.yaml. Every tunable comes from the YAML file."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, TypeVar

import yaml

T = TypeVar("T")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ClipConfig:
    pre_roll: float
    post_roll: float
    cooldown: float


@dataclass(frozen=True)
class RecorderConfig:
    segment_seconds: int
    poll_interval: float
    reconnect_attempts: int
    reconnect_delay: float
    drift_tolerance: float
    segment_wait_timeout: float


@dataclass(frozen=True)
class DetectorConfig:
    window_seconds: float
    baseline_minutes: float
    z_threshold: float
    sensitivity: dict[str, float]
    min_spike_seconds: float
    max_spike_seconds: float
    warmup_seconds: float
    min_std: float
    min_rate: float
    hype_weight: float
    caps_min_letters: int
    repeat_min: int
    top_messages: int
    hype_tokens: list[str]


@dataclass(frozen=True)
class EditorConfig:
    format: str
    width: int
    height: int
    landscape_width: int
    landscape_height: int
    caption_y_landscape: float
    fps: int
    crf: int
    preset: str
    audio_bitrate: str
    facecam_height_ratio: float
    blur_strength: int
    caption_y_no_facecam: float
    loudnorm: str


@dataclass(frozen=True)
class CaptionsConfig:
    enabled: bool
    model: str
    device: str
    language: str
    max_words: int
    max_gap: float
    font: str
    font_size: int
    font_size_landscape: int
    outline: int
    uppercase: bool
    primary_color: str
    highlight_color: str
    outline_color: str


@dataclass(frozen=True)
class FacecamDetectConfig:
    expand: float


@dataclass(frozen=True)
class FacecamBox:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class ChannelConfig:
    facecam: FacecamBox | None


@dataclass(frozen=True)
class LlmConfig:
    enabled: bool
    provider: str
    model: str


@dataclass(frozen=True)
class AppConfig:
    buffer_minutes: float
    buffer_dir: Path
    clips_dir: Path
    clip: ClipConfig
    chat_delay: dict[str, float]
    recorder: RecorderConfig
    detector: DetectorConfig
    editor: EditorConfig
    captions: CaptionsConfig
    facecam_detect: FacecamDetectConfig
    channels: dict[str, ChannelConfig]
    llm: LlmConfig

    def channel(self, name: str) -> ChannelConfig | None:
        return self.channels.get(name.lower())


def _build(cls: type[T], data: Any, section: str) -> T:
    if not isinstance(data, dict):
        raise ConfigError(f"config section '{section}' must be a mapping")
    names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    missing = names - data.keys()
    if missing:
        raise ConfigError(f"config section '{section}' is missing: {', '.join(sorted(missing))}")
    return cls(**{k: data[k] for k in names})


def _channels(data: Any) -> dict[str, ChannelConfig]:
    out: dict[str, ChannelConfig] = {}
    for name, ch in (data or {}).items():
        cam = (ch or {}).get("facecam")
        box = _build(FacecamBox, cam, f"channels.{name}.facecam") if cam else None
        out[str(name).lower()] = ChannelConfig(facecam=box)
    return out


def load_config(path: Path) -> AppConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ConfigError(f"config file not found: {path}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} is empty or not a mapping")
    base = path.parent
    try:
        return AppConfig(
            buffer_minutes=float(raw["buffer_minutes"]),
            buffer_dir=(base / raw["buffer_dir"]).resolve(),
            clips_dir=(base / raw["clips_dir"]).resolve(),
            clip=_build(ClipConfig, raw["clip"], "clip"),
            chat_delay={str(k): float(v) for k, v in raw["chat_delay"].items()},
            recorder=_build(RecorderConfig, raw["recorder"], "recorder"),
            detector=_build(DetectorConfig, raw["detector"], "detector"),
            editor=_build(EditorConfig, raw["editor"], "editor"),
            captions=_build(CaptionsConfig, raw["captions"], "captions"),
            facecam_detect=_build(FacecamDetectConfig, raw["facecam_detect"], "facecam_detect"),
            channels=_channels(raw.get("channels")),
            llm=_build(LlmConfig, raw["llm"], "llm"),
        )
    except KeyError as e:
        raise ConfigError(f"config is missing top-level key: {e.args[0]}") from e
