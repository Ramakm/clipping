"""YouTube live chat.

Primary backend: yt-dlp's live-chat downloader (maintained, already a dependency).
It appends JSON lines to `<name>.live_chat.json.part` as chat arrives; we tail it.
Fallbacks: chat-downloader, then pytchat, if yt-dlp isn't available.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from chat.base import ChatMessage, ChatSource

log = logging.getLogger(__name__)

MICROSECONDS = 1_000_000
TAIL_POLL_SECONDS = 0.5
PROCESS_STOP_TIMEOUT = 5


class YouTubeChat(ChatSource):
    name = "youtube"

    def __init__(self, url: str, video_id: str | None) -> None:
        self.url = url
        self.video_id = video_id

    @property
    def watch_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}" if self.video_id else self.url

    def messages(self, stop: threading.Event) -> Iterator[ChatMessage]:
        if not self.video_id:
            self.video_id = resolve_video_id(self.url)
        ytdlp = shutil.which("yt-dlp") or shutil.which("yt-dlp", path=str(Path(sys.executable).parent))
        if ytdlp:
            yield from self._ytdlp(ytdlp, stop)
        else:
            yield from self._chat_downloader(stop)

    # ---- yt-dlp ---------------------------------------------------------
    def _ytdlp(self, ytdlp: str, stop: threading.Event) -> Iterator[ChatMessage]:
        workdir = Path(tempfile.mkdtemp(prefix="clipper_ytchat_"))
        cmd = [ytdlp, "--quiet", "--no-warnings", "--skip-download", "--write-subs",
               "--sub-langs", "live_chat", "-o", "chat", self.watch_url]
        proc = subprocess.Popen(cmd, cwd=workdir, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        log.info("connected to youtube live chat (yt-dlp)")
        part, final = workdir / "chat.live_chat.json.part", workdir / "chat.live_chat.json"
        pos, pending = 0, b""
        try:
            while not stop.is_set():
                exited = proc.poll() is not None
                path = part if part.exists() else final
                if path.exists():
                    with path.open("rb") as f:
                        f.seek(pos)
                        data = f.read()
                    pos += len(data)
                    *lines, pending = (pending + data).split(b"\n")
                    for line in lines:
                        yield from parse_ytdlp_line(line)
                if exited:
                    if proc.returncode:
                        err = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
                        log.warning("yt-dlp chat exited: %s", err[-300:])
                    break
                stop.wait(TAIL_POLL_SECONDS)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(PROCESS_STOP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    proc.kill()
            shutil.rmtree(workdir, ignore_errors=True)

    # ---- fallbacks ------------------------------------------------------
    def _chat_downloader(self, stop: threading.Event) -> Iterator[ChatMessage]:
        try:
            from chat_downloader import ChatDownloader  # type: ignore[import-untyped]
        except ImportError:
            yield from self._pytchat(stop)
            return
        logging.getLogger("chat_downloader").setLevel(logging.WARNING)
        chat = ChatDownloader().get_chat(self.watch_url, message_groups=["messages"])
        log.info("connected to youtube live chat (chat-downloader)")
        for item in chat:
            if stop.is_set():
                break
            text = item.get("message")
            if text:
                ts_us = item.get("timestamp")
                yield ChatMessage(ts=ts_us / MICROSECONDS if ts_us else time.time(),
                                  user=(item.get("author") or {}).get("name", ""), text=str(text))

    def _pytchat(self, stop: threading.Event) -> Iterator[ChatMessage]:
        try:
            import pytchat  # type: ignore[import-untyped]
        except ImportError as e:
            raise RuntimeError("YouTube chat needs yt-dlp (or chat-downloader / pytchat)") from e
        if not self.video_id:
            raise RuntimeError("could not determine the live video id")
        chat = pytchat.create(video_id=self.video_id, interruptable=False)
        log.info("connected to youtube live chat (pytchat)")
        while chat.is_alive() and not stop.is_set():
            for c in chat.get().sync_items():
                ts = c.timestamp / 1000 if c.timestamp else time.time()
                yield ChatMessage(ts=ts, user=c.author.name, text=c.message)
        chat.terminate()


def resolve_video_id(url: str) -> str | None:
    """Turn a channel URL like youtube.com/@handle/live into the current live video id."""
    try:
        import yt_dlp  # type: ignore[import-untyped]
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
        vid = info.get("id") if info else None
        log.info("resolved %s → video %s", url, vid)
        return vid
    except Exception as e:
        log.warning("could not resolve live video id for %s: %s", url, e)
        return None


def _runs_text(runs: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for r in runs:
        if "text" in r:
            out.append(r["text"])
        elif "emoji" in r:
            e = r["emoji"]
            shortcuts = e.get("shortcuts") or []
            out.append(shortcuts[0] if shortcuts else e.get("emojiId", ""))
    return "".join(out).strip()


def parse_ytdlp_line(line: bytes) -> Iterator[ChatMessage]:
    """Yield text messages from one yt-dlp live_chat JSON line."""
    try:
        data = json.loads(line)
    except ValueError:
        return
    actions = (data.get("replayChatItemAction") or {}).get("actions") or []
    for action in actions:
        item = (action.get("addChatItemAction") or {}).get("item") or {}
        r = item.get("liveChatTextMessageRenderer")
        if not r:
            continue
        text = _runs_text((r.get("message") or {}).get("runs") or [])
        if not text:
            continue
        ts_us = r.get("timestampUsec")
        yield ChatMessage(ts=int(ts_us) / MICROSECONDS if ts_us else time.time(),
                          user=(r.get("authorName") or {}).get("simpleText", ""), text=text)
