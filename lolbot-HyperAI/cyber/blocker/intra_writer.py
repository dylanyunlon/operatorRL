"""
IntraWriter — In-process write handle bound to a Blocker.
===========================================================

Apollo reference: ``cyber/blocker/intra_writer.h``

An IntraWriter publishes messages to a Blocker channel without going
through the transport layer. Used when publisher and subscriber are
in the same process.

Claude27: New file.
Location: lolbot-HyperAI/cyber/blocker/intra_writer.py
"""

from __future__ import annotations

import time
import threading
from typing import Generic, TypeVar

from cyber.blocker.blocker import Blocker
from cyber.blocker.blocker_manager import BlockerManager

T = TypeVar("T")


class IntraWriter(Generic[T]):
    """In-process write handle for a Blocker channel.

    Apollo equivalent: ``cyber::blocker::IntraWriter<T>``

    Usage::

        writer = IntraWriter[GameSnapshot]("/lol/game_state")
        writer.Write(snapshot)  # notifies all IntraReaders
    """

    def __init__(
        self,
        channel_name: str,
        capacity: int = 16,
    ) -> None:
        self._channel_name = channel_name
        self._write_count: int = 0
        self._last_write_time: float = 0.0
        self._lock = threading.Lock()

        # Register with BlockerManager
        mgr = BlockerManager.instance()
        self._blocker: Blocker = mgr.get_or_create(
            channel_name, capacity=capacity,
        )

    @property
    def channel_name(self) -> str:
        return self._channel_name

    def Write(self, msg: T) -> bool:
        """Publish a message to the channel.

        Apollo equivalent: ``writer->Write(msg)``

        Returns True always (Blocker never rejects, may drop oldest).
        """
        self._blocker.publish(msg)
        with self._lock:
            self._write_count += 1
            self._last_write_time = time.time()
        return True

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "channel_name": self._channel_name,
                "write_count": self._write_count,
                "last_write_time": self._last_write_time,
            }

    def __repr__(self) -> str:
        return f"<IntraWriter channel={self._channel_name!r}>"
