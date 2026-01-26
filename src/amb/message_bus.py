"""
Message Bus Implementation

Thread-safe pub/sub message bus for agent communication.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from queue import Queue
from threading import Lock
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4


class MessageType(Enum):
    """Types of messages in the system."""
    CLAIM = "claim"
    OBSERVATION = "observation"
    VERIFICATION_RESULT = "verification_result"
    ALERT = "alert"
    SYSTEM = "system"


@dataclass
class Message:
    """
    A message passed between agents on the bus.
    
    Attributes:
        type: The message category
        topic: The topic channel
        payload: The actual data
        source: Originating agent ID
        timestamp: When the message was created
        correlation_id: Links related messages
        message_id: Unique identifier
    """
    type: MessageType
    topic: str
    payload: Dict[str, Any]
    source: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    correlation_id: Optional[str] = None
    message_id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> Dict[str, Any]:
        """Serialize message to dictionary."""
        return {
            "message_id": self.message_id,
            "type": self.type.value,
            "topic": self.topic,
            "payload": self.payload,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "correlation_id": self.correlation_id,
        }


class MessageBus:
    """
    Thread-safe publish/subscribe message bus.
    
    Agents publish messages to topics, and other agents subscribe
    to receive messages on those topics.
    """

    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Message], None]]] = {}
        self._queues: Dict[str, Queue] = {}
        self._lock = Lock()
        self._message_history: List[Message] = []
        self._max_history = 1000

    def subscribe(
        self,
        topic: str,
        callback: Callable[[Message], None],
        agent_id: Optional[str] = None
    ) -> None:
        """
        Subscribe to a topic with a callback function.
        
        Args:
            topic: The topic to subscribe to
            callback: Function called when a message arrives
            agent_id: Optional identifier for the subscribing agent
        """
        with self._lock:
            if topic not in self._subscribers:
                self._subscribers[topic] = []
            self._subscribers[topic].append(callback)

    def subscribe_queue(self, topic: str, agent_id: str) -> Queue:
        """
        Subscribe to a topic with a queue for polling.
        
        Args:
            topic: The topic to subscribe to
            agent_id: Identifier for the subscribing agent
            
        Returns:
            Queue that will receive messages
        """
        with self._lock:
            queue_key = f"{topic}:{agent_id}"
            if queue_key not in self._queues:
                self._queues[queue_key] = Queue()
            
            # Also add a callback to push to the queue
            def queue_callback(msg: Message):
                self._queues[queue_key].put(msg)
            
            if topic not in self._subscribers:
                self._subscribers[topic] = []
            self._subscribers[topic].append(queue_callback)
            
            return self._queues[queue_key]

    def publish(self, message: Message) -> None:
        """
        Publish a message to a topic.
        
        All subscribers to that topic will receive the message.
        
        Args:
            message: The message to publish
        """
        with self._lock:
            # Store in history
            self._message_history.append(message)
            if len(self._message_history) > self._max_history:
                self._message_history.pop(0)

            # Notify subscribers
            if message.topic in self._subscribers:
                for callback in self._subscribers[message.topic]:
                    try:
                        callback(message)
                    except Exception as e:
                        print(f"[AMB] Error delivering message to subscriber: {e}")

    def get_history(
        self,
        topic: Optional[str] = None,
        limit: int = 100
    ) -> List[Message]:
        """
        Get message history, optionally filtered by topic.
        
        Args:
            topic: Filter by this topic (None for all)
            limit: Maximum messages to return
            
        Returns:
            List of historical messages
        """
        with self._lock:
            if topic:
                filtered = [m for m in self._message_history if m.topic == topic]
            else:
                filtered = self._message_history.copy()
            return filtered[-limit:]

    def clear(self) -> None:
        """Clear all subscriptions and history."""
        with self._lock:
            self._subscribers.clear()
            self._queues.clear()
            self._message_history.clear()
