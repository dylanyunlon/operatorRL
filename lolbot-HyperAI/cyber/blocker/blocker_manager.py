"""
BlockerManager — Singleton registry of all Blockers.
======================================================

Apollo reference: ``cyber/blocker/blocker_manager.h``

Central registry that maps channel names to Blocker instances.
When a Writer publishes to a channel, BlockerManager finds the
Blocker for that channel and calls publish(). When a Reader
subscribes, BlockerManager wires the callback.

Claude27: New file.
Location: lolbot-HyperAI/cyber/blocker/blocker_manager.py
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional, Type

from cyber.blocker.blocker import Blocker, BlockerAttr

logger = logging.getLogger(__name__)


class BlockerManager:
    """Singleton registry of all Blockers.

    Apollo equivalent: ``cyber::blocker::BlockerManager``

    Usage::

        mgr = BlockerManager.instance()
        mgr.get_or_create("/lol/game_state", capacity=16)
        mgr.publish("/lol/game_state", snapshot)
        mgr.subscribe("/lol/game_state", "prediction", callback)
    """

    _instance: Optional["BlockerManager"] = None
    _init_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "BlockerManager":
        """Get or create the singleton instance."""
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        with cls._init_lock:
            if cls._instance is not None:
                cls._instance.shutdown()
            cls._instance = None

    def __init__(self) -> None:
        self._blockers: Dict[str, Blocker] = {}
        self._lock = threading.Lock()

    def get_or_create(
        self,
        channel_name: str,
        capacity: int = 16,
    ) -> Blocker:
        """Get existing Blocker or create a new one for channel_name.

        Apollo equivalent: ``BlockerManager::GetOrCreateBlocker()``
        """
        with self._lock:
            if channel_name in self._blockers:
                return self._blockers[channel_name]
            attr = BlockerAttr(
                channel_name=channel_name,
                capacity=capacity,
            )
            blocker: Blocker = Blocker(attr)
            self._blockers[channel_name] = blocker
            logger.debug("Created Blocker for channel %r", channel_name)
            return blocker

    def get_blocker(self, channel_name: str) -> Optional[Blocker]:
        """Get a Blocker by channel name, or None if not registered."""
        with self._lock:
            return self._blockers.get(channel_name)

    def publish(self, channel_name: str, msg: Any) -> bool:
        """Publish a message to a channel's Blocker.

        Returns True if a Blocker exists for the channel.
        """
        with self._lock:
            blocker = self._blockers.get(channel_name)
        if blocker is None:
            return False
        blocker.publish(msg)
        return True

    def subscribe(
        self,
        channel_name: str,
        subscriber_name: str,
        callback: Callable,
        capacity: int = 16,
    ) -> bool:
        """Subscribe to a channel. Creates Blocker if needed.

        Returns True if subscription succeeded.
        """
        blocker = self.get_or_create(channel_name, capacity)
        return blocker.subscribe(subscriber_name, callback)

    def unsubscribe(self, channel_name: str, subscriber_name: str) -> bool:
        """Unsubscribe from a channel."""
        with self._lock:
            blocker = self._blockers.get(channel_name)
        if blocker is None:
            return False
        return blocker.unsubscribe(subscriber_name)

    def remove_blocker(self, channel_name: str) -> bool:
        """Remove a Blocker entirely."""
        with self._lock:
            blocker = self._blockers.pop(channel_name, None)
        if blocker is not None:
            blocker.clear_subscribers()
            blocker.clear_messages()
            return True
        return False

    def channel_names(self) -> list:
        """List all registered channel names."""
        with self._lock:
            return list(self._blockers.keys())

    def shutdown(self) -> None:
        """Clear all blockers and subscribers."""
        with self._lock:
            for blocker in self._blockers.values():
                blocker.clear_subscribers()
                blocker.clear_messages()
            self._blockers.clear()
        logger.info("BlockerManager shutdown complete")

    def snapshot(self) -> dict:
        """Return serializable status of all blockers."""
        with self._lock:
            return {
                "blocker_count": len(self._blockers),
                "channels": {
                    name: blocker.snapshot()
                    for name, blocker in self._blockers.items()
                },
            }

    def __repr__(self) -> str:
        with self._lock:
            return f"<BlockerManager blockers={len(self._blockers)}>"
