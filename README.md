# Live Stream Auto-Clipper

Give it a **live** Twitch or YouTube link and get ready-to-post **vertical 9:16 shorts**
(YouTube Shorts / Reels / TikTok) while the stream is still live. It watches chat for hype
moments (chat suddenly explodes) or clips on demand, adds word-by-word captions and
normalises the audio. 100% free and local, with no API keys.

```
/clip https://twitch.tv/<channel>
```

## Quick start

**1. Install (one command, macOS / Linux / WSL):**

```bash
curl -fsSL https://raw.githubusercontent.com/Ramakm/clipping/main/install.sh | bash
```

This installs the tool into `~/.clipper`, adds a `clipper` command to `~/.local/bin`, and adds
the `/clip` slash command to Claude Code and Codex. Re-run it any time to update.

**2. Clip** from your coding agent or the terminal:

| Where | Command |
|---|---|
| **Claude Code** | `/clip https://twitch.tv/<channel>` |
| **Codex** | `/prompts:clip https://twitch.tv/<channel>` |
| **Terminal** | `clipper https://twitch.tv/<channel> --now --clip-length 30` |

**3. Get your clips** from `./clips` in the folder you ran it from.

### Saying what you want

The slash command understands plain words after the link:

| You type | What happens |
|---|---|
| `/clip <link>` | Clips the next 30 seconds right now |
| `/clip <link> 45 sec` | Clips the next 45 seconds right now |
| `/clip <link> auto` | Waits for a chat hype moment and clips it (up to 30 min) |
| `/clip <link> auto 3 clips 20 sec` | Catches 3 hype moments, 20 seconds each |
| `/clip <link> no captions` | Faster, skips captions |

Supported links: `twitch.tv/<channel>`, `youtube.com/watch?v=<id>`, `youtube.com/@<handle>/live`,
`youtube.com/live/<id>`, `youtu.be/<id>`.

### Claude Code plugin (alternative)

```
/plugin install clipper --marketplace Ramakm/clipping
```

The command is then `/clipper:clip <link>`. On Claude Code older than 2.1.275, run
`/plugin marketplace add Ramakm/clipping` and then `/plugin install clipper@clipping`.
The first `/clip` installs the `clipper` tool itself if it isn't there yet.

### Requirements

- **Python 3.11+** and **git**
- **ffmpeg**:

  | OS | Command |
  |---|---|
  | macOS | `brew install ffmpeg-full` (the installer finds it automatically) |
  | Ubuntu/Debian | `sudo apt install ffmpeg` |
  | Fedora | `sudo dnf install ffmpeg` (RPM Fusion) |
  | Windows | `winget install Gyan.FFmpeg`, then use WSL or the manual setup below |

  Burning captions into the video needs an ffmpeg with **libass**. The plain Homebrew `ffmpeg`
  formula doesn't have it. Without libass, clips still work and captions are saved next to
  each clip as a `.ass` subtitle file.

The first clip downloads the caption model (about 500 MB, one time only).

## What you get

Each clip is `clip_<n>_<tag>.mp4` (1080x1920, 30fps, H.264/AAC, −14 LUFS loudness) plus
`clip_<n>_<tag>.json`:

```json
{ "channel": "somestreamer", "spike_time": "2026-11-19T01:03:12+05:30", "score": 54.46,
  "duration": 30.0, "top_messages": ["OMEGALUL", "KEKW"], "transcript": "No way. Did you see that?",
  "suggested_title": "No way | somestreamer" }
```

- **Layout:** facecam on top (1/3) and gameplay below (2/3) when the channel has a facecam box
  configured; otherwise the full frame over a blurred background.
- **Captions:** big, bold, up to 3 words at a time, with the spoken word highlighted.

## Configuration

Installed users edit `~/.clipper/config.yaml`; if you cloned the repo, edit `config.yaml`.

**Allowlist and facecam:** only clip streamers who **allow clipping**. List them under
`channels:` (unlisted channels get a warning). Add a facecam box, in source pixels, to get the
facecam-on-top layout:

```yaml
channels:
  somestreamer:
    facecam: { x: 0, y: 0, w: 480, h: 270 }
```

To find the box automatically:

```bash
~/.clipper/.venv/bin/pip install mediapipe
clipper https://twitch.tv/somestreamer --detect-facecam
```

It logs a `facecam:` line to paste into the config.

**Other settings** (all in the same file): clip pre/post roll, cooldown between clips,
detection sensitivity (`detector.z_threshold`, lower means more clips), hype words, video
quality, caption font, colours and size, and the caption model (`captions.model: base` is
faster on slow machines).

## Terminal usage

```
clipper <url> [--now] [--clip-length SEC] [--max-clips N] [--out DIR] [--timeout MIN]
              [--channel NAME] [--detect-facecam] [--no-captions] [--warmup SEC] [-v]
```

| Option | Meaning |
|---|---|
| `--now` | Skip detection; clip the next `--clip-length` seconds and exit |
| `--clip-length SEC` | Total clip length (default 40 = 30s before the spike + 10s after) |
| `--max-clips N` | Stop after N clips |
| `--timeout MIN` | Give up after MIN minutes |
| `--out DIR` | Where to save clips (default `clips/` next to the config) |
| `--channel NAME` | Channel name for allowlist/facecam lookup (useful for YouTube video links) |
| `--no-captions` | Skip captions (faster) |
| `--warmup SEC` | Override how much chat history is needed before detecting (default 120) |

Without `--now`, it runs until you press **Ctrl+C**, which finishes the clip in progress and
cleans up. It reconnects if the stream drops and exits when the stream ends. While running it
shows a status line:

```
● LIVE  chat 42 msg/s  baseline 9  clips 3
```

## Update / uninstall

```bash
# update
curl -fsSL https://raw.githubusercontent.com/Ramakm/clipping/main/install.sh | bash

# uninstall
rm -rf ~/.clipper ~/.local/bin/clipper ~/.claude/skills/clip ~/.codex/prompts/clip.md
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `clipper: command not found` | Add `export PATH="$HOME/.local/bin:$PATH"` to your shell profile, or use `~/.clipper/bin/clipper` |
| "this ffmpeg has no libass" | Captions are saved as a `.ass` file. `brew install ffmpeg-full` (macOS), then re-run the installer |
| "could not open the stream" | The stream is offline, the link is wrong, or it's geo-blocked |
| YouTube clips arrive later | Normal. YouTube delivers chat about 15–20s late, so detection waits for it (`chat_delay` in config) |
| Slow captions | Set `captions.model: base` in the config, or use `--no-captions` |

## How it works

```
stream URL ─► recorder.py   streamlink/yt-dlp → ffmpeg segment muxer → rolling buffer of 10s .ts chunks
           └► chat/          Twitch IRC (anonymous) / YouTube (yt-dlp live chat) → ChatMessage queue
                 ▼
           hype_detector.py  5s msg rate vs rolling baseline (z-score) + hype tokens → spike
                 ▼
           cutter.py         concat buffered segments for [spike − pre_roll, spike + post_roll]
           captions.py       faster-whisper word timestamps → karaoke-style ASS subtitles
           editor.py         facecam/gameplay stack (or blurred bg) + captions + loudnorm → 9:16 mp4
```

Detection runs on the main thread and editing runs in a worker thread, so detection never blocks.

| Path | Purpose |
|---|---|
| `clipper.py` | Entry point and orchestration (live, `--now`, and offline replay modes) |
| `recorder.py`, `segments.py` | Live recording into a rolling buffer, wall-clock mapping |
| `chat/` | `ChatSource` interface plus Twitch, YouTube and replay adapters |
| `hype_detector.py` | Pure spike detection logic (unit tested) |
| `cutter.py`, `editor.py`, `captions.py` | Cut, vertical edit, captions |
| `pipeline.py` | Clip worker: cut → captions → edit → mp4 + json |
| `install.sh`, `skills/clip/`, `integrations/codex/`, `.claude-plugin/` | Installer and agent integrations |

## Development

Manual setup from a clone:

```bash
git clone https://github.com/Ramakm/clipping && cd clipping
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python clipper.py https://twitch.tv/<channel> --now --clip-length 30
```

**Tests:** `pytest`

**Offline replay** tests the full pipeline without a live stream:

```bash
python scripts/make_sample.py --minutes 5 --out samples      # synthetic video + chat with 2 hype bursts
python clipper.py --file samples/sample.mp4 --chat samples/sample_chat.json --channel somestreamer
```

`--chat` takes a JSON list of `{"t": seconds, "user": "...", "text": "..."}` (or chat-downloader's
format). Replay runs faster than real time.

## Notes

- Only clip streamers who allow it, and respect each platform's terms.
- Clips are saved locally; nothing is uploaded.
- LLM-written titles (`llm.enabled`) are planned; v1 uses a local heuristic.
