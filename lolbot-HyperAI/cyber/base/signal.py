"""
Signal — Thread-safe Observer pattern for component communication.
====================================================================

Apollo reference: ``cyber/base/signal.h``

Claude27: New file. Fills Apollo cyber/base/ gap.
Location: lolbot-HyperAI/cyber/base/signal.py
"""

from __future__ import annotations

import logging
import threading
import weakref
from dataclasses import dataclass
from typing import Any, Callable, Dict, Generic, List, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_next_connection_id = 0
_connection_id_lock = threading.Lock()


def _gen_connection_id() -> int:
    global _next_connection_id
    with _connection_id_lock:
        _next_connection_id += 1
        return _next_connection_id


class Connection:
    """Handle to a signal-slot connection.

    Apollo equivalent: ``cyber::base::Connection``
    Call ``disconnect()`` to remove the slot from the signal.
    """

    __slots__ = ("_id", "_signal_ref", "_connected")

    def __init__(self, conn_id: int, signal: "Signal") -> None:
        self._id = conn_id
        self._signal_ref = weakref.ref(signal)
        self._connected = True

    @property
    def id(self) -> int:
        return self._id

    @property
    def connected(self) -> bool:
        return self._connected

    def disconnect(self) -> None:
        if not self._connected:
            return
        sig = self._signal_ref()
        if sig is not None:
            sig._remove_slot(self._id)
        self._connected = False

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, *args: Any) -> None:
        self.disconnect()

    def __repr__(self) -> str:
        return f"<Connection id={self._id} connected={self._connected}>"


@dataclass
class _Slot:
    id: int
    callback: Callable
    once: bool = False


@dataclass
class SignalStats:
    emit_count: int = 0
    slot_error_count: int = 0
    total_slots_fired: int = 0


class Signal(Generic[T]):
    """Thread-safe signal/event emitter.

    Apollo equivalent: ``cyber::base::Signal<Args...>``

    Usage::

        sig = Signal[str]("game_phase_changed")
        conn = sig.connect(my_callback)
        sig.emit("InProgress")
        conn.disconnect()
    """

    def __init__(self, name: str = "unnamed_signal") -> None:
        self._name = name
        self._slots: Dict[int, _Slot] = {}
        self._lock = threading.Lock()
        self.stats = SignalStats()

    @property
    def name(self) -> str:
        return self._name

    @property
    def slot_count(self) -> int:
        with self._lock:
            return len(self._slots)

    def connect(self, callback: Callable[..., Any]) -> Connection:
        """Connect a callback slot to this signal."""
        conn_id = _gen_connection_id()
        slot = _Slot(id=conn_id, callback=callback)
        with self._lock:
            self._slots[conn_id] = slot
        return Connection(conn_id, self)

    def connect_once(self, callback: Callable[..., Any]) -> Connection:
        """Connect a one-shot callback (auto-disconnects after first emit)."""
        conn_id = _gen_connection_id()
        slot = _Slot(id=conn_id, callback=callback, once=True)
        with self._lock:
            self._slots[conn_id] = slot
        return Connection(conn_id, self)

    def disconnect_all(self) -> int:
        """Disconnect all slots. Returns count removed."""
        with self._lock:
            count = len(self._slots)
            self._slots.clear()
            return count

    def _remove_slot(self, conn_id: int) -> None:
        with self._lock:
            self._slots.pop(conn_id, None)

    def emit(self, *args: Any, **kwargs: Any) -> int:
        """Emit signal, calling all connected slots synchronously.

        Slot exceptions are logged but do NOT block subsequent slots.
        Returns number of slots invoked successfully.
        """
        self.stats.emit_count += 1
        with self._lock:
            slots = list(self._slots.values())

        fired = 0
        to_remove: List[int] = []
        for slot in slots:
            try:
                slot.callback(*args, **kwargs)
                fired += 1
                self.stats.total_slots_fired += 1
            except Exception as exc:
                self.stats.slot_error_count += 1
                logger.error(
                    "[Signal:%s] Slot %d raised %s: %s",
                    self._name, slot.id, type(exc).__name__, exc,
                )
            if slot.once:
                to_remove.append(slot.id)

        if to_remove:
            with self._lock:
                for conn_id in to_remove:
                    self._slots.pop(conn_id, None)
        return fired

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "name": self._name,
                "slot_count": len(self._slots),
                "emit_count": self.stats.emit_count,
                "slot_error_count": self.stats.slot_error_count,
            }

    def __repr__(self) -> str:
        return f"<Signal name={self._name!r} slots={self.slot_count}>"
