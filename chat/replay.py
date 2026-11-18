"""Load a recorded chat log for offline replay.

Accepted formats (a JSON list):
  [{"t": 12.5, "user": "bob", "text": "KEKW"}, ...]                     # our format
  [{"time_in_seconds": 12.5, "author": {"name": "bob"}, "message": "..."}] # chat-downloader
Timestamps are seconds from the start of the video.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chat.base import ChatMessage


def _parse(item: dict[str, Any]) -> ChatMessage | None:
    if "t" in item:
        return ChatMessage(ts=float(item["t"]), user=str(item.get("user", "")), text=str(item.get("text", "")))
    if "time_in_seconds" in item:
        author = item.get("author") or {}
        return ChatMessage(ts=float(item["time_in_seconds"]), user=str(author.get("name", "")),
                           text=str(item.get("message", "")))
    return None


def load_chat_file(path: Path) -> list[ChatMessage]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list of messages")
    msgs = [m for item in data if isinstance(item, dict) and (m := _parse(item)) is not None]
    return sorted(msgs, key=lambda m: m.ts)
