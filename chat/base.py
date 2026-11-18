"""Platform-agnostic chat interface. The detector only ever sees ChatMessage."""

from __future__ import annotations

import logging
import queue
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatMessage:
    ts: float  # local wall-clock seconds (time.time()), same clock as the recorder
    user: str
    text: str


class ChatSource(ABC):
    """A live chat feed. Implementations must stop promptly once `stop` is set."""

    name: str = "chat"

    @abstractmethod
    def messages(self, stop: threading.Event) -> Iterator[ChatMessage]:
        """Yield messages until the chat ends or `stop` is set."""

    def pump(self, out: "queue.Queue[ChatMessage]", stop: threading.Event) -> None:
        """Thread target: forward every message into `out`."""
        try:
            for msg in self.messages(stop):
                out.put(msg)
                if stop.is_set():
                    break
        except Exception:  # keep the clipper alive; video keeps recording
            log.exception("%s chat reader crashed", self.name)
        log.info("%s chat reader stopped", self.name)
