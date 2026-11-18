"""Twitch chat via anonymous, read-only IRC (no OAuth needed)."""

from __future__ import annotations

import logging
import random
import re
import socket
import ssl
import threading
import time
from collections.abc import Iterator

from chat.base import ChatMessage, ChatSource

log = logging.getLogger(__name__)

HOST = "irc.chat.twitch.tv"
PORT = 6697
SOCKET_TIMEOUT = 1.0          # lets the read loop notice `stop` quickly
MAX_BACKOFF = 30.0

_PRIVMSG = re.compile(r"^(?:@\S+ )?:(?P<user>[^!\s]+)!\S+ PRIVMSG #\S+ :(?P<text>.*)$")


def parse_privmsg(line: str) -> tuple[str, str] | None:
    """Return (user, text) for a PRIVMSG line, else None."""
    m = _PRIVMSG.match(line)
    if not m:
        return None
    return m.group("user"), m.group("text").strip()


class TwitchChat(ChatSource):
    name = "twitch"

    def __init__(self, channel: str) -> None:
        self.channel = channel.lower().lstrip("#")

    def messages(self, stop: threading.Event) -> Iterator[ChatMessage]:
        backoff = 1.0
        while not stop.is_set():
            try:
                yield from self._session(stop)
                backoff = 1.0
            except (OSError, ssl.SSLError) as e:
                log.warning("twitch chat disconnected (%s); retrying in %.0fs", e, backoff)
                stop.wait(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)

    def _session(self, stop: threading.Event) -> Iterator[ChatMessage]:
        ctx = ssl.create_default_context()
        raw = socket.create_connection((HOST, PORT), timeout=10)
        with ctx.wrap_socket(raw, server_hostname=HOST) as sock:
            sock.settimeout(SOCKET_TIMEOUT)
            nick = f"justinfan{random.randint(10000, 99999)}"
            sock.sendall(f"NICK {nick}\r\nJOIN #{self.channel}\r\n".encode())
            log.info("joined twitch chat #%s as %s", self.channel, nick)
            buf = b""
            while not stop.is_set():
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    raise ConnectionError("server closed connection")
                buf += data
                *lines, buf = buf.split(b"\r\n")
                for raw_line in lines:
                    line = raw_line.decode("utf-8", errors="replace")
                    if line.startswith("PING"):
                        sock.sendall(line.replace("PING", "PONG", 1).encode() + b"\r\n")
                    elif " RECONNECT" in line:
                        raise ConnectionError("server requested reconnect")
                    elif (parsed := parse_privmsg(line)) is not None:
                        yield ChatMessage(ts=time.time(), user=parsed[0], text=parsed[1])
