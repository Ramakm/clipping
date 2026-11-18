"""Chat sources + URL → platform resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from chat.base import ChatMessage, ChatSource

__all__ = ["ChatMessage", "ChatSource", "StreamTarget", "parse_stream_url", "create_chat_source"]


@dataclass(frozen=True)
class StreamTarget:
    platform: str          # "twitch" | "youtube"
    url: str
    channel: str           # allowlist key (twitch login, youtube handle, or video id)
    video_id: str | None = None


def parse_stream_url(url: str) -> StreamTarget:
    u = urlparse(url if "://" in url else f"https://{url}")
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    parts = [p for p in u.path.split("/") if p]
    if host.endswith("twitch.tv") and parts:
        return StreamTarget("twitch", url, parts[0].lower())
    if host == "youtu.be" and parts:
        return StreamTarget("youtube", url, parts[0], parts[0])
    if host.endswith("youtube.com"):
        if parts and parts[0] == "watch":
            vid = parse_qs(u.query).get("v", [""])[0]
            return StreamTarget("youtube", url, vid, vid or None)
        if len(parts) >= 2 and parts[0] == "live":
            return StreamTarget("youtube", url, parts[1], parts[1])
        if parts and re.match(r"^@", parts[0]):
            return StreamTarget("youtube", url, parts[0].lower())
    raise ValueError(f"unsupported stream URL: {url} (expected twitch.tv/<channel> or a YouTube live URL)")


def create_chat_source(target: StreamTarget) -> ChatSource:
    if target.platform == "twitch":
        from chat.twitch import TwitchChat
        return TwitchChat(target.channel)
    from chat.youtube import YouTubeChat
    return YouTubeChat(target.url, target.video_id)
