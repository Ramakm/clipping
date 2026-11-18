# CLAUDE.md — Live Stream Auto-Clipper

## What this project is
A local Python tool that watches a **live** stream, reads its live chat, detects
"hype moments" (chat suddenly explodes), and automatically produces
ready-to-post **vertical short-form clips** (YouTube Shorts / Reels / TikTok)
while the stream is still live.

Goal: clips posted minutes after a moment happens, not hours after the stream ends.

```
python clipper.py https://twitch.tv/<channel>
python clipper.py https://youtube.com/watch?v=<live_id>
```
Output: finished MP4s + metadata in `clips/`.

## Scope
### v1 (build this first)
- Platforms: **Twitch** (primary), **YouTube Live** (second adapter)
- CLI only, one stream at a time
- Rolling recording buffer → chat spike detection → cut → auto-edit → save
- 100% free / local. No paid APIs required.

### Later (do NOT build unless asked)
- Web dashboard to preview/approve clips
- Auto-upload to YouTube Shorts / Instagram / TikTok
- Multiple streams in parallel
- LLM ranking + titles via Claude API (must stay optional, behind a config flag)

## Pipeline
```
stream URL
  ├─► recorder.py      streamlink/yt-dlp → ffmpeg segment muxer → buffer/ (rolling 10s .ts chunks)
  └─► chat_reader.py   Twitch IRC / YouTube live chat → timestamped messages (queue)
                          │
                          ▼
                    hype_detector.py   sliding-window msg rate + z-score + hype keywords
                          │ spike event (timestamp, score, top messages)
                          ▼
                    cutter.py          stitch buffer chunks for [spike - pre_roll, spike + post_roll]
                          ▼
                    editor.py          9:16 layout + facecam + captions + loudnorm
                          ▼
                    clips/clip_<n>_<tag>.mp4  +  clip_<n>_<tag>.json
```

## Project structure
```
clipper.py          # entry point, argument parsing, orchestrates threads
recorder.py         # live stream → rolling segment buffer
chat/
  base.py           # ChatSource interface: yields ChatMessage(ts, user, text)
  twitch.py         # anonymous IRC (irc.chat.twitch.tv:6697, nick justinfan<random>)
  youtube.py        # chat-downloader / pytchat
hype_detector.py    # spike detection logic (pure, unit-testable)
cutter.py           # extract time range from buffer segments
editor.py           # ffmpeg filter graphs: crop, stack, captions, audio
captions.py         # faster-whisper → word-level ASS subtitles
config.yaml         # all tunables
clips/              # output (gitignored)
buffer/             # rolling segments (gitignored)
tests/
```

## Core components — rules and details

### Recorder
- `streamlink <url> best -O | ffmpeg -i - -c copy -f segment -segment_time 10 -reset_timestamps 1 buffer/seg_%06d.ts`
- Use `yt-dlp` for YouTube if streamlink fails.
- Keep only the last `buffer_minutes` (default 5) of segments; delete older ones.
- Track wall-clock start time of each segment so chat timestamps map to video time.
- Auto-reconnect if the stream drops; exit cleanly when stream ends.

### Chat reader
- Each platform implements `ChatSource` in `chat/base.py`. Detector must not know which platform it is.
- Twitch: read-only anonymous login, no OAuth needed. Handle PING/PONG.
- All timestamps in local wall-clock seconds (`time.time()`), same clock as recorder.

### Hype detector
- Keep a sliding window of messages/sec. Baseline = rolling mean + std over last N minutes.
- Spike when `rate > baseline_mean + z_threshold * baseline_std` for `min_spike_seconds`.
- Boost score for hype tokens (configurable list): `KEKW, LUL, LMAO, OMEGALUL, Pog, PogChamp, W, CLIP, CLIP IT, OMG, WTF, NO WAY`, all-caps, repeated chars.
- Chat lags the actual moment by ~5–15s → clip window is `[spike_start - pre_roll, spike_start + post_roll]` (defaults 30s / 10s).
- Cooldown between clips (default 60s) so one moment = one clip.
- Ignore the first `warmup_seconds` (baseline not ready).
- Keep this module **pure** (no I/O) so it can be unit-tested with fake message streams.

### Cutter
- Concatenate the needed buffer segments (ffmpeg concat demuxer), then trim precisely.
- Wait until post_roll segments exist before cutting.
- Copy segments out of `buffer/` immediately so rotation can't delete them mid-edit.

### Editor (output: 1080x1920, 30fps, H.264 + AAC)
- Layout: facecam on top (~1/3 height), gameplay center-cropped to fill bottom (~2/3).
- Facecam position: fixed crop box from `config.yaml` per channel. Optional `--detect-facecam` uses MediaPipe on a sample frame to suggest the box.
- If no facecam configured: blurred-background full-gameplay layout.
- Audio: `loudnorm=I=-14:TP=-1.5:LRA=11` (short-form standard).
- Captions: burn ASS subtitles from `captions.py`. Bold, large, centered between facecam and gameplay, max ~3 words on screen, highlight active word.
- Encode with `-preset veryfast -crf 20` by default.

### Captions
- `faster-whisper`, model from config (`small` default; `base` on CPU-only machines).
- Use GPU (`cuda`, `float16`) if available, else `cpu`, `int8`. Detect automatically.
- Word-level timestamps → ASS file.

### Metadata JSON per clip
```json
{ "channel": "", "spike_time": "", "score": 0, "duration": 40,
  "top_messages": [], "transcript": "", "suggested_title": "" }
```
`suggested_title` = simple heuristic in v1; Claude API only if `llm.enabled: true`.

## config.yaml (shape)
```yaml
buffer_minutes: 5
clip: { pre_roll: 30, post_roll: 10, cooldown: 60 }
detector: { window_seconds: 5, baseline_minutes: 3, z_threshold: 3.0,
            min_spike_seconds: 4, warmup_seconds: 120, hype_tokens: [...] }
editor: { width: 1080, height: 1920, crf: 20, preset: veryfast }
captions: { enabled: true, model: small, language: auto }
channels:
  somestreamer: { facecam: { x: 0, y: 0, w: 480, h: 270 } }
llm: { enabled: false, provider: anthropic, model: claude-haiku-5-5 }
```

## Tech stack
- Python 3.11+
- `streamlink`, `yt-dlp`, `ffmpeg` (system binary, must be on PATH)
- `faster-whisper`, `mediapipe` (optional), `chat-downloader` or `pytchat`
- `pyyaml`, `rich` (console output), `pytest`
- Threads/queues for recorder + chat + processing; editing runs in a worker so detection never blocks.

## Coding conventions
- Type hints everywhere, small functions, dataclasses for `ChatMessage`, `SpikeEvent`, `ClipJob`.
- All tunables in `config.yaml` — no magic numbers in code.
- Build ffmpeg commands as argument lists (never shell strings); log the full command on failure.
- `logging` module, not print. Clear one-line console status: `● LIVE  chat 42 msg/s  baseline 9  clips 3`.
- Graceful Ctrl+C: stop threads, finish in-progress clip, clean `buffer/`.
- Cross-platform paths (`pathlib`) — must work on Windows, macOS, Linux.

## Testing
- Unit test `hype_detector` with synthetic chat streams (calm → burst → calm).
- Test `cutter` time-mapping with fake segment timestamps.
- Integration: `python clipper.py --file sample.mp4 --chat sample_chat.json` replays a recorded
  stream + chat offline so the pipeline can be tested without a live stream.

## Rules / guardrails
- Only clip streamers who **allow clipping**. Keep a `channels:` allowlist in config; warn if the
  channel isn't listed.
- Never commit `clips/`, `buffer/`, `.env`, or API keys.
- No auto-uploading in v1.
- Keep the free/local path working at all times; paid APIs are optional extras only.

## Build order
1. `recorder.py` + rolling buffer (verify segments appear and rotate)
2. `chat/twitch.py` (print live messages)
3. `hype_detector.py` + tests
4. `cutter.py` (raw clips saved on spikes)
5. `editor.py` vertical layout + loudnorm
6. `captions.py` + burn-in
7. `chat/youtube.py`
8. Offline replay mode, README with setup steps (ffmpeg install per OS)
