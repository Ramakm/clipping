import dataclasses
import random

from chat.base import ChatMessage
from hype_detector import HypeClassifier, HypeDetector


def run(detector, schedule, seed=0):
    """schedule: list of (seconds, msgs_per_second, text). Returns spikes."""
    rng = random.Random(seed)
    spikes, t = [], 0
    for seconds, rate, text in schedule:
        for _ in range(seconds):
            n = rng.choice([max(rate - 1, 0), rate, rate + 1]) if rate else 0
            for k in range(n):
                detector.add(ChatMessage(t + k / max(n, 1), f"u{k}", text))
            t += 1
            if (s := detector.tick(t)) is not None:
                spikes.append(s)
    return spikes


def test_calm_burst_calm_fires_once(cfg):
    det = HypeDetector(cfg.detector, cfg.clip.cooldown)
    spikes = run(det, [(180, 2, "hello"), (15, 25, "KEKW"), (60, 2, "ok")])
    assert len(spikes) == 1
    s = spikes[0]
    assert 180 <= s.start <= 186
    assert s.detected_at - s.start >= cfg.detector.min_spike_seconds
    assert s.tag == "KEKW"
    assert s.top_messages[0] == "KEKW"
    assert s.hype_ratio > 0.5


def test_calm_chat_never_fires(cfg):
    det = HypeDetector(cfg.detector, cfg.clip.cooldown)
    assert run(det, [(600, 3, "chatting")]) == []


def test_warmup_suppresses_early_burst(cfg):
    det = HypeDetector(cfg.detector, cfg.clip.cooldown)
    assert run(det, [(30, 1, "hi"), (15, 30, "OMG")]) == []


def test_short_blip_below_min_spike_seconds_ignored(cfg):
    det = HypeDetector(cfg.detector, cfg.clip.cooldown)
    # 1s burst only lifts the 5s window for a few ticks; make it tiny
    assert run(det, [(180, 2, "hi"), (1, 12, "W"), (60, 2, "hi")]) == []


def test_cooldown_merges_close_bursts(cfg):
    det = HypeDetector(cfg.detector, cfg.clip.cooldown)
    spikes = run(det, [(180, 2, "hi"), (10, 25, "LUL"), (20, 2, "hi"), (10, 25, "LUL"), (60, 2, "hi")])
    assert len(spikes) == 1


def test_bursts_after_cooldown_both_fire(cfg):
    det = HypeDetector(cfg.detector, cfg.clip.cooldown)
    spikes = run(det, [(180, 2, "hi"), (10, 25, "LUL"), (120, 2, "hi"), (10, 25, "Pog"), (30, 2, "hi")])
    assert len(spikes) == 2
    assert spikes[1].start - spikes[0].start >= cfg.clip.cooldown


def test_hype_text_scores_higher(cfg):
    plain = run(HypeDetector(cfg.detector, cfg.clip.cooldown), [(180, 2, "hi"), (15, 25, "nice play")])
    hype = run(HypeDetector(cfg.detector, cfg.clip.cooldown), [(180, 2, "hi"), (15, 25, "NO WAY")])
    assert plain and hype
    assert hype[0].score > plain[0].score


def test_dead_chat_min_std_floor(cfg):
    det = HypeDetector(cfg.detector, cfg.clip.cooldown)
    # totally silent then 2 msgs/s: below min_rate-adjusted threshold? min_std keeps it sane
    assert run(det, [(180, 0, ""), (10, 1, "hi")]) == []


def test_classifier(cfg):
    c = HypeClassifier(cfg.detector)
    assert c.classify("KEKW") == (("KEKW",), True)
    assert c.classify("clip it chat") [1]
    assert "CLIP IT" in c.classify("clip it chat")[0]
    assert c.classify("LOOOOL")[1]          # repeated chars
    assert c.classify("WHAT IS THIS")[1]    # all caps
    assert not c.classify("what a nice day")[1]
    assert c.classify("W")[1]
    assert not c.classify("wow")[0]         # 'W' token must be a whole word


def test_custom_threshold(cfg):
    strict = dataclasses.replace(cfg.detector, z_threshold=50.0)
    det = HypeDetector(strict, cfg.clip.cooldown)
    assert run(det, [(180, 2, "hi"), (15, 25, "KEKW")]) == []
