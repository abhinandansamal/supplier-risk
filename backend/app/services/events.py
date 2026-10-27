"""Live events of running investigations, for the server-sent events stream.

One channel per queued or running investigation keeps its events so far (bounded) and feeds
every subscriber. A subscriber first gets the history, then live events, and last a "done"
event. Finished investigations have no channel; their stored trace is replayed instead.
All of it lives in the one API process (decision 0008, SEC-024).
"""

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

# Security: bounds the memory of one investigation's live stream (T8).
MAX_EVENTS_PER_CHANNEL = 500

Event = tuple[str, dict[str, Any]]


class Subscription:
    """The events one SSE client will receive, in order."""

    def __init__(self, events: Iterable[Event] = ()) -> None:
        """Start with the given events already waiting."""
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        for event in events:
            self._queue.put_nowait(event)

    def push(self, event: Event) -> None:
        """Queue one event."""
        self._queue.put_nowait(event)

    async def next(self, timeout: float) -> Event | None:
        """Return the next event, or None if none arrives within the timeout."""
        try:
            return await asyncio.wait_for(self._queue.get(), timeout)
        except TimeoutError:
            return None


@dataclass
class _Channel:
    history: list[Event] = field(default_factory=list)
    subscribers: set[Subscription] = field(default_factory=set)


class EventHub:
    """Channels of the investigations that are queued or running."""

    def __init__(self) -> None:
        """Start without channels."""
        self._channels: dict[str, _Channel] = {}

    def open(self, investigation_id: str) -> None:
        """Create the channel of a newly queued investigation."""
        self._channels.setdefault(investigation_id, _Channel())

    def is_open(self, investigation_id: str) -> bool:
        """Return True while the investigation is queued or running."""
        return investigation_id in self._channels

    def publish(self, investigation_id: str, kind: str, data: dict[str, Any]) -> None:
        """Send an event to the channel's subscribers (ignored without a channel)."""
        channel = self._channels.get(investigation_id)
        if channel is None or len(channel.history) >= MAX_EVENTS_PER_CHANNEL:
            return
        event = (kind, data)
        channel.history.append(event)
        for subscriber in channel.subscribers:
            subscriber.push(event)

    def close(self, investigation_id: str, status: str) -> None:
        """Send "done" with the final status and remove the channel."""
        channel = self._channels.pop(investigation_id, None)
        if channel is None:
            return
        for subscriber in channel.subscribers:
            subscriber.push(("done", {"status": status}))

    def subscribe(self, investigation_id: str) -> Subscription | None:
        """Return a subscription with the history so far, or None without a channel."""
        channel = self._channels.get(investigation_id)
        if channel is None:
            return None
        subscription = Subscription(channel.history)
        channel.subscribers.add(subscription)
        return subscription

    def unsubscribe(self, investigation_id: str, subscription: Subscription) -> None:
        """Stop feeding a subscription."""
        channel = self._channels.get(investigation_id)
        if channel is not None:
            channel.subscribers.discard(subscription)
