"""Chat spike detection. Pure logic — no I/O, no clocks; callers pass `now`.

Usage: call `add(msg)` for every chat message and `tick(now)` about once per
second. `tick` returns a SpikeEvent when a hype moment is detected.
"""

from __future__ import annotations

import math
import re
from collections import Counter, deque
from dataclasses import dataclass

from chat.base import ChatMessage
from config import DetectorConfig


@dataclass(frozen=True)
class SpikeEvent:
    start: float               # when the rate first crossed the threshold
    detected_at: float
    score: float
    rate: float                # msg/s at detection
    baseline_mean: float
    baseline_std: float
    hype_ratio: float          # share of spike messages that look like hype
    top_messages: tuple[str, ...]
    tag: str                   # most common hype token, used in the filename


@dataclass(frozen=True)
class _Msg:
    ts: float
    text: str
    tokens: tuple[str, ...]    # hype tokens found in this message
    hype: bool


class HypeClassifier:
    """Decides whether a single message is 'hype' and which tokens it contains."""

    def __init__(self, cfg: DetectorConfig) -> None:
        self._tokens = [(t, re.compile(rf"(?<!\w){re.escape(t)}(?!\w)", re.IGNORECASE)) for t in cfg.hype_tokens]
        self._caps_min = cfg.caps_min_letters
        self._repeat = re.compile(rf"(\w)\1{{{max(cfg.repeat_min - 1, 1)},}}", re.IGNORECASE)

    def classify(self, text: str) -> tuple[tuple[str, ...], bool]:
        hits = [tok for tok, rx in self._tokens if rx.search(text)]
        # "CLIP IT" also matches "CLIP" — keep only the most specific tokens
        found = tuple(t for t in hits if not any(t != o and t.lower() in o.lower() for o in hits))
        letters = [c for c in text if c.isalpha()]
        all_caps = len(letters) >= self._caps_min and all(c.isupper() for c in letters)
        repeated = bool(self._repeat.search(text))
        return found, bool(found) or all_caps or repeated


class HypeDetector:
    def __init__(self, cfg: DetectorConfig, cooldown: float) -> None:
        self.cfg = cfg
        self.cooldown = cooldown
        self._classifier = HypeClassifier(cfg)
        self._msgs: deque[_Msg] = deque()
        self._samples: deque[tuple[float, float]] = deque()  # (time, rate) for the baseline
        self._first_tick: float | None = None
        self._above_since: float | None = None
        self._z_sum = 0.0
        self._z_n = 0
        self._fired_current = False   # this burst already produced (or was denied) a clip
        self._last_fire_start: float | None = None
        # exposed for the status line
        self.rate = 0.0
        self.baseline_mean = 0.0
        self.baseline_std = 0.0

    # ---- input ---------------------------------------------------------
    def add(self, msg: ChatMessage) -> None:
        tokens, hype = self._classifier.classify(msg.text)
        self._msgs.append(_Msg(msg.ts, msg.text.strip(), tokens, hype))

    # ---- evaluation ----------------------------------------------------
    def tick(self, now: float) -> SpikeEvent | None:
        if self._first_tick is None:
            self._first_tick = now
        self._prune(now)
        rate = self._rate(now)
        mean, std = self._baseline()
        self.rate, self.baseline_mean, self.baseline_std = rate, mean, std

        threshold = mean + self.cfg.z_threshold * max(std, self.cfg.min_std)
        warm = now - self._first_tick >= self.cfg.warmup_seconds
        above = warm and rate > threshold and rate >= self.cfg.min_rate

        if not above:
            self._reset_burst()
            self._samples.append((now, rate))
            return None

        if self._above_since is None:
            self._above_since = now
        self._z_sum += (rate - mean) / max(std, self.cfg.min_std)
        self._z_n += 1
        if now - self._above_since > self.cfg.max_spike_seconds:
            self._samples.append((now, rate))  # sustained level = new normal

        if self._fired_current or now - self._above_since < self.cfg.min_spike_seconds:
            return None
        self._fired_current = True
        if self._last_fire_start is not None and self._above_since - self._last_fire_start < self.cooldown:
            return None  # same moment (or too soon) — skip this whole burst
        self._last_fire_start = self._above_since
        return self._make_event(now, rate, mean, std)

    # ---- helpers -------------------------------------------------------
    def _reset_burst(self) -> None:
        self._above_since = None
        self._fired_current = False
        self._z_sum = 0.0
        self._z_n = 0

    def _prune(self, now: float) -> None:
        keep_msgs = now - (self.cfg.window_seconds + self.cfg.max_spike_seconds)
        while self._msgs and self._msgs[0].ts < keep_msgs:
            self._msgs.popleft()
        keep_samples = now - self.cfg.baseline_minutes * 60
        while self._samples and self._samples[0][0] < keep_samples:
            self._samples.popleft()

    def _rate(self, now: float) -> float:
        lo = now - self.cfg.window_seconds
        return sum(1 for m in self._msgs if lo < m.ts <= now) / self.cfg.window_seconds

    def _baseline(self) -> tuple[float, float]:
        rates = [r for _, r in self._samples]
        if not rates:
            return 0.0, 0.0
        mean = sum(rates) / len(rates)
        var = sum((r - mean) ** 2 for r in rates) / len(rates)
        return mean, math.sqrt(var)

    def _make_event(self, now: float, rate: float, mean: float, std: float) -> SpikeEvent:
        assert self._above_since is not None
        lo = self._above_since - self.cfg.window_seconds
        burst = [m for m in self._msgs if lo < m.ts <= now]
        hype_ratio = sum(m.hype for m in burst) / len(burst) if burst else 0.0
        mean_z = self._z_sum / max(self._z_n, 1)
        score = mean_z * (1 + self.cfg.hype_weight * hype_ratio)
        return SpikeEvent(
            start=self._above_since,
            detected_at=now,
            score=round(score, 2),
            rate=round(rate, 2),
            baseline_mean=round(mean, 2),
            baseline_std=round(std, 2),
            hype_ratio=round(hype_ratio, 2),
            top_messages=self._top_messages(burst),
            tag=self._tag(burst),
        )

    def _top_messages(self, burst: list[_Msg]) -> tuple[str, ...]:
        counts: Counter[str] = Counter()
        display: dict[str, str] = {}
        for m in burst:
            key = " ".join(m.text.lower().split())
            if key:
                counts[key] += 1
                display.setdefault(key, m.text)
        return tuple(display[k] for k, _ in counts.most_common(self.cfg.top_messages))

    @staticmethod
    def _tag(burst: list[_Msg]) -> str:
        counts = Counter(tok for m in burst for tok in m.tokens)
        return counts.most_common(1)[0][0] if counts else "hype"
