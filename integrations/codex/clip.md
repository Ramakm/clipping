---
description: Clip a live Twitch/YouTube stream into reels/shorts or landscape clips
argument-hint: URL=<live-url> [now|auto] [reel|landscape] [N sec] [N clips]
---

Clip a live stream with the local `clipper` tool. Request: $ARGUMENTS

1. Check the tool exists: `command -v clipper || ls ~/.clipper/bin/clipper`. If missing, install it:
   `curl -fsSL https://raw.githubusercontent.com/Ramakm/clipping/main/install.sh | bash`
   (use `~/.clipper/bin/clipper` if `clipper` is not on PATH).
2. From the request take:
   - the stream URL (Twitch or YouTube live; ask if missing)
   - the mode: `now` (default) clips the next N seconds immediately; `auto`/`hype` waits for a chat spike
   - the format: reel/short/tiktok/vertical → `--format vertical`; landscape/horizontal/16:9 →
     `--format landscape`. If not stated, ask once: "Reel/Short (vertical 9:16) or landscape (16:9)?"
   - the length (default 30 seconds), and for auto mode the number of clips (default 1) and sensitivity
     ("more clips" → `--sensitivity high`, "only big moments" → `--sensitivity low`)
3. Run, writing clips to `./clips`:
   - now:  `clipper <URL> --now --clip-length <LEN> --format <FORMAT> --out ./clips`
   - auto: `clipper <URL> --max-clips <N> --clip-length <LEN> --format <FORMAT> --timeout 30 --out ./clips`
     (auto mode can take many minutes; it needs ~2 min of chat history and logs a `status:` line every 30s)
4. Report each new `clips/*.mp4` with its duration, `suggested_title` and a transcript snippet from the
   matching `.json`. If the log says ffmpeg lacks libass, captions are in a `.ass` sidecar
   (`brew install ffmpeg-full` on macOS burns them in). If the channel isn't in the allowlist, remind the
   user to only clip streamers who allow it (`channels:` in `~/.clipper/config.yaml`).
