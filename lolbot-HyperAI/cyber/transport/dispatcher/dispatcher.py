#!/usr/bin/env python3
"""
cyber/transport/dispatcher/dispatcher.py — Apollo-style Message Dispatcher
============================================================================

从 Apollo `cyber/transport/dispatcher/dispatcher.cc` 这个好例子开始。然后,
遵循该模式实现一个新的 `Dispatcher`, 让系统可以按照channel分发消息给订阅者。

Apollo reference:
    cyber/transport/dispatcher/dispatcher.cc   — Dispatcher class
    cyber/transport/dispatcher/dispatcher.h    — AddListener/RemoveListener

位置: lolbot-HyperAI/cyber/transport/dispatcher/dispatcher.py

Claude29: New file — fills gap vs Apollo Dispatcher.
         Based on Claude27 blocker layer, pure addition.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generic, List, Optional, Set, TypeVar
from abc import ABC, abstractmethod

from cyber.base.atomic_rw_lock import AtomicRWLock

T = TypeVar('T')

# Type alias for message listener
MessageListener = Callable[[Any, 'MessageInfo'], None]


@dataclass
class MessageInfo:
    """
    Information about a message delivery.
    
    Apollo equivalent: cyber/transport/message/message_info.h
    """
    channel_id: int = 0
    channel_name: str = ""
    sender_id: int = 0
    seq_num: int = 0
    timestamp_ns: int = 0
    
    def to_dict(self) -> Dict:
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "sender_id": self.sender_id,
            "seq_num": self.seq_num,
            "timestamp_ns": self.timestamp_ns,
        }


@dataclass
class RoleAttributes:
    """
    Attributes describing a reader/writer role.
    
    Apollo equivalent: cyber/proto/role_attributes.proto
    """
    id: int = 0
    channel_id: int = 0
    channel_name: str = ""
    node_name: str = ""
    host_name: str = ""
    process_id: int = 0
    
    @staticmethod
    def from_channel(channel_name: str, node_name: str = "") -> RoleAttributes:
        """Create attributes from channel name."""
        return RoleAttributes(
            id=hash(f"{node_name}:{channel_name}") & 0xFFFFFFFF,
            channel_id=hash(channel_name) & 0xFFFFFFFF,
            channel_name=channel_name,
            node_name=node_name,
        )


class ListenerHandlerBase(ABC):
    """
    Base class for listener handlers.
    
    Apollo equivalent: cyber/transport/message/listener_handler.h
    """
    
    @abstractmethod
    def connect(self, listener_id: int, listener: MessageListener) -> None:
        pass
    
    @abstractmethod
    def disconnect(self, listener_id: int) -> None:
        pass
    
    @abstractmethod
    def run(self, message: Any, info: MessageInfo) -> None:
        pass


class ListenerHandler(ListenerHandlerBase, Generic[T]):
    """
    Handler for listeners of a specific message type.
    
    Apollo equivalent: cyber/transport/message/listener_handler.h ListenerHandler<T>
    """
    
    def __init__(self) -> None:
        self._listeners: Dict[int, MessageListener] = {}
        self._lock = threading.Lock()
    
    def connect(self, listener_id: int, listener: MessageListener) -> None:
        """Connect a listener.
        
        Apollo equivalent: ListenerHandler::Connect()
        """
        with self._lock:
            self._listeners[listener_id] = listener
    
    def disconnect(self, listener_id: int) -> None:
        """Disconnect a listener.
        
        Apollo equivalent: ListenerHandler::Disconnect()
        """
        with self._lock:
            self._listeners.pop(listener_id, None)
    
    def run(self, message: T, info: MessageInfo) -> None:
        """Deliver message to all connected listeners.
        
        Apollo equivalent: ListenerHandler::Run()
        """
        with self._lock:
            listeners = list(self._listeners.values())
        
        for listener in listeners:
            try:
                listener(message, info)
            except Exception:
                pass  # Don't let one listener crash others


class Dispatcher:
    """
    Message dispatcher for channel-based pub/sub.
    
    Apollo equivalent: cyber/transport/dispatcher/dispatcher.cc
    
    The Dispatcher manages message routing from publishers to subscribers.
    Each channel has a ListenerHandler that holds all subscribers for that
    channel.
    
    Usage::
    
        dispatcher = Dispatcher.instance()
        
        # Add a listener
        attrs = RoleAttributes.from_channel("/lol/game_state", "perception")
        dispatcher.add_listener(attrs, my_callback)
        
        # Dispatch a message
        dispatcher.dispatch("/lol/game_state", game_state)
        
        # Remove listener
        dispatcher.remove_listener(attrs)
    """
    
    _instance: Optional[Dispatcher] = None
    _instance_lock = threading.Lock()
    
    @classmethod
    def instance(cls) -> Dispatcher:
        """Get singleton instance."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.shutdown()
            cls._instance = None
    
    def __init__(self) -> None:
        self._is_shutdown = False
        self._rw_lock = AtomicRWLock()
        self._msg_listeners: Dict[int, ListenerHandlerBase] = {}
        self._seq_counter = 0
        
        # Statistics
        self._stats = {
            "total_dispatched": 0,
            "total_listeners_added": 0,
            "total_listeners_removed": 0,
        }
    
    def shutdown(self) -> None:
        """Shutdown the dispatcher.
        
        Apollo equivalent: Dispatcher::Shutdown()
        """
        self._is_shutdown = True
        with self._rw_lock.write_lock():
            self._msg_listeners.clear()
    
    def add_listener(
        self,
        self_attr: RoleAttributes,
        listener: MessageListener,
    ) -> None:
        """Add a message listener.
        
        Apollo equivalent: Dispatcher::AddListener()
        
        Args:
            self_attr: Attributes of the subscriber
            listener: Callback function(message, info)
        """
        if self._is_shutdown:
            return
        
        channel_id = self_attr.channel_id
        
        with self._rw_lock.write_lock():
            if channel_id not in self._msg_listeners:
                self._msg_listeners[channel_id] = ListenerHandler()
            
            handler = self._msg_listeners[channel_id]
            handler.connect(self_attr.id, listener)
            self._stats["total_listeners_added"] += 1
    
    def remove_listener(self, self_attr: RoleAttributes) -> None:
        """Remove a message listener.
        
        Apollo equivalent: Dispatcher::RemoveListener()
        
        Args:
            self_attr: Attributes of the subscriber to remove
        """
        if self._is_shutdown:
            return
        
        channel_id = self_attr.channel_id
        
        with self._rw_lock.write_lock():
            if channel_id in self._msg_listeners:
                self._msg_listeners[channel_id].disconnect(self_attr.id)
                self._stats["total_listeners_removed"] += 1
    
    def has_channel(self, channel_id: int) -> bool:
        """Check if a channel has any listeners.
        
        Apollo equivalent: Dispatcher::HasChannel()
        """
        with self._rw_lock.read_lock():
            return channel_id in self._msg_listeners
    
    def dispatch(
        self,
        channel_name: str,
        message: Any,
        sender_id: int = 0,
    ) -> int:
        """Dispatch a message to all listeners on a channel.
        
        Args:
            channel_name: Channel to dispatch to
            message: The message to send
            sender_id: ID of the sender
        
        Returns:
            Number of listeners that received the message
        """
        if self._is_shutdown:
            return 0
        
        channel_id = hash(channel_name) & 0xFFFFFFFF
        
        with self._rw_lock.read_lock():
            handler = self._msg_listeners.get(channel_id)
            if handler is None:
                return 0
            
            self._seq_counter += 1
            info = MessageInfo(
                channel_id=channel_id,
                channel_name=channel_name,
                sender_id=sender_id,
                seq_num=self._seq_counter,
                timestamp_ns=0,
            )
            
            handler.run(message, info)
            self._stats["total_dispatched"] += 1
            return 1
    
    @property
    def is_shutdown(self) -> bool:
        return self._is_shutdown
    
    def stats(self) -> Dict:
        """Get dispatcher statistics."""
        with self._rw_lock.read_lock():
            return {
                "is_shutdown": self._is_shutdown,
                "channel_count": len(self._msg_listeners),
                **self._stats,
            }


# ─── Intra-Process Dispatcher ──────────────────────────────────────────────

class IntraDispatcher(Dispatcher):
    """
    Intra-process dispatcher for same-process communication.
    
    Apollo equivalent: cyber/transport/dispatcher/intra_dispatcher.cc
    
    Optimized for zero-copy message passing within the same process.
    """
    
    _instance: Optional[IntraDispatcher] = None
    
    @classmethod
    def instance(cls) -> IntraDispatcher:
        """Get singleton instance."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    
    def dispatch(
        self,
        channel_name: str,
        message: Any,
        sender_id: int = 0,
    ) -> int:
        """Dispatch message within same process (zero-copy).
        
        Apollo equivalent: IntraDispatcher::OnMessage()
        """
        # For intra-process, we pass the message directly (no serialization)
        return super().dispatch(channel_name, message, sender_id)
