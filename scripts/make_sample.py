"""Generate a synthetic sample.mp4 + sample_chat.json for offline replay testing.

    python scripts/make_sample.py --minutes 5 --out samples/

Video: 1920x1080 test pattern (has a built-in timestamp) + tone. Chat: calm chatter with
two hype bursts (at 2:40 and 4:00) so the detector has something to find.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
from pathlib import Path

CALM_RATE = 2
BURST_RATE = 25
BURST_SECONDS = 12
CALM = ["hi chat", "lol", "nice", "what game is this", "gg", "hello", "true", "ok"]
HYPE = ["KEKW", "NO WAY", "OMEGALUL", "CLIP IT", "WTF", "LMAOOOO", "Pog"]


def make_video(path: Path, seconds: int) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30:duration={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
           "-c:v", "libx264", "-preset", "veryfast", "-g", "60", "-c:a", "aac", "-shortest", str(path)]
    subprocess.run(cmd, check=True)


def make_chat(seconds: int, bursts: list[int], seed: int = 1) -> list[dict]:
    rng = random.Random(seed)
    msgs = []
    for t in range(seconds):
        in_burst = any(b <= t < b + BURST_SECONDS for b in bursts)
        n = BURST_RATE if in_burst else rng.randint(CALM_RATE - 1, CALM_RATE + 1)
        pool = HYPE if in_burst else CALM
        for _ in range(n):
            msgs.append({"t": round(t + rng.random(), 3), "user": f"viewer{rng.randint(1, 500)}",
                         "text": rng.choice(pool)})
    return sorted(msgs, key=lambda m: m["t"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=5)
    ap.add_argument("--out", type=Path, default=Path("samples"))
    args = ap.parse_args()
    seconds = int(args.minutes * 60)
    args.out.mkdir(parents=True, exist_ok=True)
    bursts = [b for b in (160, 240) if b + BURST_SECONDS < seconds]
    make_video(args.out / "sample.mp4", seconds)
    (args.out / "sample_chat.json").write_text(json.dumps(make_chat(seconds, bursts), indent=0))
    print(f"wrote {args.out / 'sample.mp4'} and {args.out / 'sample_chat.json'} (bursts at {bursts}s)")


if __name__ == "__main__":
    main()
