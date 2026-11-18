---
name: clip
description: Clip a live Twitch or YouTube stream into vertical 9:16 reels/shorts or landscape 16:9 clips (captions, -14 LUFS audio) with the local `clipper` tool. Use when the user runs /clip or asks to clip, capture, or make a short/reel from a live stream link.
argument-hint: <twitch-or-youtube-live-url> [now | auto] [reel | landscape] [N sec] [N clips]
---

# /clip — live stream → vertical shorts

The user's request: `$ARGUMENTS`

## 1. Make sure the tool is installed

Run `command -v clipper || ls ~/.clipper/bin/clipper`. If neither exists, install it
(tell the user it takes a few minutes the first time):

```bash
curl -fsSL https://raw.githubusercontent.com/Ramakm/clipping/main/install.sh | bash
```

Use `~/.clipper/bin/clipper` if `clipper` is not on PATH yet.

## 2. Work out what they want

From `$ARGUMENTS` pick out:
- **URL** (required): `twitch.tv/<channel>`, `youtube.com/watch?v=…`, `youtube.com/@handle/live`, `youtu.be/…`.
  If missing, ask for it.
- **Mode**:
  - `now` (default) — clip the next N seconds immediately. Fast and predictable.
  - `auto` / `hype` / "best moments" — watch chat and clip when it explodes (needs ~2 min of
    chat history first, then waits for a real spike; can take a while).
- **Format**:
  - "reel", "short", "tiktok", "vertical", "9:16" → `--format vertical` (1080x1920)
  - "landscape", "horizontal", "youtube video", "16:9", "wide" → `--format landscape` (1920x1080)
  - **Not stated → ask the user once before running**: "Reel/Short (vertical 9:16) or landscape
    (16:9)?" Use your question tool if you have one. If you can't ask (non-interactive run),
    use vertical.
- **Length**: "30 sec", "45s" → `--clip-length`. Default 30.
- **Count** (auto mode only): "3 clips" → `--max-clips`. Default 1.
- **Sensitivity** (auto mode only): "more clips", "catch smaller moments", "sensitive" →
  `--sensitivity high`; "only big moments" → `--sensitivity low`. Default: leave it out (medium).

## 3. Run it

Clips are written to `./clips` in the current folder.

- now mode (finishes in about length + 30s):
  ```bash
  clipper <URL> --now --clip-length <LEN> --format <FORMAT> --out ./clips
  ```
- auto mode — **run in the background**, it can take many minutes:
  ```bash
  clipper <URL> --max-clips <N> --clip-length <LEN> --format <FORMAT> --timeout 30 --out ./clips
  ```
  It logs a `status:` line every 30s (chat msg/s, baseline, and the rate needed for a spike).
  If the user asks how it's going, report the latest one. If chat stays far below the spike
  level, suggest re-running with `--sensitivity high` or switching to now mode.

Add `--no-captions` if the user wants it faster or doesn't want captions.

## 4. Report back

- List the new `clips/*.mp4` files and read each matching `.json`: report duration,
  `suggested_title`, and a short transcript snippet.
- If the log says the ffmpeg has no libass, captions were saved as a `.ass` sidecar —
  mention `brew install ffmpeg-full` (macOS) to burn them in.
- If the log warns the channel is not in the allowlist, remind the user to only clip
  streamers who allow it, and that they can add the channel (and its facecam box, found
  with `--detect-facecam`) under `channels:` in `~/.clipper/config.yaml` for a
  facecam-on-top layout.
- If the stream is offline / the URL can't be opened, say so plainly.
