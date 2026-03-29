"""
Notification Service — Event broadcasting and alert management.

Provides pub/sub notification channels for evolution events,
anomaly alerts, and performance report delivery.

Location: agentos/governance/notification_service.py

Reference (拿来主义):
  - operatorRL: event callback pattern (_fire_evolution)
  - Akagi: bridge message queue broadcasting pattern
  - agentlightning/runner/game_runner.py: event notification
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.notification_service.v1"


class NotificationService:
    """Pub/sub notification service for AgentOS events.

    Supports channel-based subscriptions, broadcasting,
    direct sends, and message history.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def subscribe(self, channel: str, callback: Callable) -> None:
        """Subscribe to a notification channel.

        Args:
            channel: Channel name.
            callback: Callable receiving message string.
        """
        self._subscribers[channel].append(callback)

    def unsubscribe(self, channel: str, callback: Callable) -> None:
        """Unsubscribe from a notification channel.

        Args:
            channel: Channel name.
            callback: Callback to remove.
        """
        if channel in self._subscribers:
            self._subscribers[channel] = [
                cb for cb in self._subscribers[channel] if cb is not callback
            ]

    def subscriber_count(self, channel: str) -> int:
        """Number of subscribers on a channel."""
        return len(self._subscribers.get(channel, []))

    def broadcast(self, channel: str, message: str) -> None:
        """Broadcast a message to all subscribers on a channel.

        Args:
            channel: Channel name.
            message: Message string.
        """
        self._history[channel].append({
            "message": message,
            "timestamp": time.time(),
        })
        for cb in self._subscribers.get(channel, []):
            try:
                cb(message)
            except Exception as e:
                logger.warning("Subscriber callback error: %s", e)

    def send(self, channel: str, message: str) -> dict[str, Any]:
        """Send a message (logged to history, not broadcast).

        Args:
            channel: Channel name.
            message: Message string.

        Returns:
            Status dict with 'sent' bool.
        """
        self._history[channel].append({
            "message": message,
            "timestamp": time.time(),
        })
        self._fire_evolution("notification_sent", {
            "channel": channel,
            "message_preview": message[:50],
        })
        return {"sent": True, "channel": channel}

    def get_history(self, channel: str) -> list[dict[str, Any]]:
        """Get message history for a channel.

        Args:
            channel: Channel name.

        Returns:
            List of {message, timestamp} dicts.
        """
        return list(self._history.get(channel, []))

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })
