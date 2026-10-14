"""In-memory publish/subscribe used for the Server-Sent Events stream."""

from __future__ import annotations

import asyncio
from collections import defaultdict


class Broker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, username: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers[username.lower()].add(queue)
        return queue

    def unsubscribe(self, username: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(username.lower())
        if subscribers:
            subscribers.discard(queue)
            if not subscribers:
                del self._subscribers[username.lower()]

    def publish(self, username: str, event: str, data: dict) -> None:
        for queue in list(self._subscribers.get(username.lower(), ())):
            try:
                queue.put_nowait((event, data))
            except asyncio.QueueFull:
                pass
