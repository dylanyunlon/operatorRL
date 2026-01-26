"""
Base Agent Class

Foundation class for all agents in the Carbon Auditor Swarm.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from threading import Thread, Event
from typing import Any, Dict, List, Optional
from uuid import uuid4

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amb import MessageBus, Message, MessageType


class AgentState(Enum):
    """Agent lifecycle states."""
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class AgentMetrics:
    """Runtime metrics for an agent."""
    messages_received: int = 0
    messages_sent: int = 0
    errors: int = 0
    start_time: Optional[datetime] = None
    last_activity: Optional[datetime] = None


class Agent(ABC):
    """
    Base class for all agents in the swarm.
    
    Agents are autonomous workers that:
    - Subscribe to topics on the message bus
    - Process messages using their tools
    - Publish results back to the bus
    
    Lifecycle:
        1. Created with agent_id and bus reference
        2. start() - begins processing in a thread
        3. stop() - gracefully shuts down
    """

    def __init__(
        self,
        agent_id: str,
        bus: MessageBus,
        name: Optional[str] = None,
    ):
        """
        Initialize an agent.
        
        Args:
            agent_id: Unique identifier for this agent
            bus: Reference to the message bus
            name: Human-readable name (defaults to agent_id)
        """
        self.agent_id = agent_id
        self.bus = bus
        self.name = name or agent_id
        
        self._state = AgentState.CREATED
        self._thread: Optional[Thread] = None
        self._stop_event = Event()
        self._metrics = AgentMetrics()

    @property
    def state(self) -> AgentState:
        """Current agent state."""
        return self._state

    @property
    def metrics(self) -> AgentMetrics:
        """Agent metrics."""
        return self._metrics

    @property
    @abstractmethod
    def subscribed_topics(self) -> List[str]:
        """Topics this agent subscribes to."""
        pass

    @abstractmethod
    def handle_message(self, message: Message) -> None:
        """
        Process a received message.
        
        Args:
            message: The message to process
        """
        pass

    def start(self) -> None:
        """Start the agent in a background thread."""
        if self._state != AgentState.CREATED:
            raise RuntimeError(f"Cannot start agent in state {self._state}")

        self._state = AgentState.STARTING
        self._metrics.start_time = datetime.utcnow()
        
        # Subscribe to topics
        for topic in self.subscribed_topics:
            self.bus.subscribe(topic, self._on_message, self.agent_id)

        self._state = AgentState.RUNNING
        self._log(f"Agent started, subscribed to: {self.subscribed_topics}")

    def run_threaded(self) -> Thread:
        """Start the agent in a daemon thread and return the thread."""
        self.start()
        
        def run_loop():
            while not self._stop_event.is_set():
                self._stop_event.wait(0.1)
        
        self._thread = Thread(target=run_loop, daemon=True)
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        """Stop the agent gracefully."""
        self._state = AgentState.STOPPING
        self._stop_event.set()
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        
        self._state = AgentState.STOPPED
        self._log("Agent stopped")

    def _on_message(self, message: Message) -> None:
        """
        Internal message handler with metrics and error handling.
        """
        self._metrics.messages_received += 1
        self._metrics.last_activity = datetime.utcnow()
        
        try:
            self.handle_message(message)
        except Exception as e:
            self._metrics.errors += 1
            self._log(f"Error handling message: {e}", level="ERROR")
            self._state = AgentState.ERROR

    def publish(
        self,
        topic: str,
        payload: Dict[str, Any],
        message_type: MessageType = MessageType.SYSTEM,
        correlation_id: Optional[str] = None,
    ) -> None:
        """
        Publish a message to the bus.
        
        Args:
            topic: The topic to publish to
            payload: The message data
            message_type: Type of message
            correlation_id: Optional correlation ID for request tracking
        """
        message = Message(
            type=message_type,
            topic=topic,
            payload=payload,
            source=self.agent_id,
            correlation_id=correlation_id,
        )
        
        self.bus.publish(message)
        self._metrics.messages_sent += 1
        self._metrics.last_activity = datetime.utcnow()

    def _log(self, message: str, level: str = "INFO") -> None:
        """Log a message with agent context."""
        timestamp = datetime.utcnow().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{timestamp}] [{level}] [{self.name}] {message}")
