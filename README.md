# Live Stream Auto-Clipper

Watches a **live** Twitch or YouTube stream, reads its chat, detects hype moments
(chat suddenly explodes), and writes ready-to-post **vertical 9:16 shorts** to
`clips/` while the stream is still live. 100% free and local.

```
python clipper.py https://twitch.tv/<channel>
python clipper.py https://youtube.com/watch?v=<live_id>
python clipper.py https://youtube.com/@<handle>/live
```

```
● LIVE  chat 42 msg/s  baseline 9  clips 3
```

Each clip is `clips/clip_<n>_<tag>.mp4` (1080x1920, 30fps, H.264/AAC, −14 LUFS)
plus `clip_<n>_<tag>.json`:

```json
{ "channel": "somestreamer", "spike_time": "2026-11-19T01:03:12+05:30", "score": 54.46,
  "duration": 40.0, "top_messages": ["OMEGALUL", "KEKW"], "transcript": "No way. Did you see that?",
  "suggested_title": "No way | somestreamer" }
```

## How it works

```
stream URL ─► recorder.py   streamlink/yt-dlp → ffmpeg segment muxer → buffer/ (rolling 10s .ts)
           └► chat/          Twitch IRC (anonymous) / YouTube (yt-dlp live chat) → ChatMessage queue
                 ▼
           hype_detector.py  5s msg rate vs rolling baseline (z-score) + hype tokens
                 ▼ spike
           cutter.py         concat buffer segments for [spike − 30s, spike + 10s]
           captions.py       faster-whisper word timestamps → karaoke ASS subtitles
           editor.py         facecam-on-top / gameplay-below (or blurred bg) + captions + loudnorm
```

Detection runs on the main thread; cutting/encoding runs in a worker thread, so detection never blocks.

## Setup

### 1. ffmpeg (must be on PATH)

Caption burn-in needs an ffmpeg built with **libass** (the `ass` filter). Check with
`ffmpeg -filters | grep " ass "`. Without it, clips still render and the captions are
saved next to them as a `.ass` sidecar.

| OS | Command |
|---|---|
| macOS | `brew install ffmpeg-full` and put it on PATH (`brew info ffmpeg-full` shows how). The plain `ffmpeg` formula has no libass. |
| Ubuntu/Debian | `sudo apt install ffmpeg` |
| Fedora | `sudo dnf install ffmpeg` (RPM Fusion) |
| Windows | `winget install Gyan.FFmpeg` (the "full" build includes libass) |

### 2. Python 3.11+

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install mediapipe              # optional, for --detect-facecam
```

The first clip downloads the whisper model (`small` ≈ 500 MB). On a slow CPU-only
machine set `captions.model: base` in `config.yaml`.

### 3. Configure your channel

Only clip streamers who **allow clipping**. Add them to the `channels:` allowlist in
`config.yaml` (you'll get a warning otherwise). The facecam box is in source pixels:

```yaml
channels:
  somestreamer:
    facecam: { x: 0, y: 0, w: 480, h: 270 }
```

No facecam box means a blurred-background layout. To find the box, run once with
`--detect-facecam`; it logs a suggested `facecam:` line to paste into the config.

## Usage

```
python clipper.py <url> [--channel NAME] [--detect-facecam] [--no-captions] [--warmup SECONDS] [-v]
```

- Clips start after `detector.warmup_seconds` (120s) of chat history.
- Ctrl+C stops recording, finishes the clip in progress and cleans `buffer/`.
- Recording reconnects automatically if the stream drops, and exits when the stream ends.

All tunables (pre/post roll, cooldown, z-threshold, hype tokens, encode settings,
caption style, …) live in `config.yaml`.

## Offline replay (no live stream needed)

```bash
python scripts/make_sample.py --minutes 5 --out samples      # synthetic video + chat with 2 bursts
python clipper.py --file samples/sample.mp4 --chat samples/sample_chat.json --channel somestreamer
```

`--chat` takes a JSON list of `{"t": seconds, "user": "...", "text": "..."}` (or
chat-downloader's output format). Replay runs faster than real time.

## Tests

```bash
pytest
```

## Notes

- **YouTube chat** is read through yt-dlp, and YouTube delivers it about 15–20s late.
  `chat_delay.youtube` (25s) makes the detector evaluate that far in the past so
  late messages still count. Clips appear correspondingly later.
- LLM titles (`llm.enabled`) are planned but not part of v1; titles use a local heuristic.
- No auto-uploading in v1.
