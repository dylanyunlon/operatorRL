"""
AMB Core - A lightweight, broker-agnostic message bus for AI Agents.

AMB (Agent Message Bus) provides a decoupled communication layer that allows
AI agents to emit signals, broadcast intentions, and coordinate without tight
coupling between senders and receivers.

Key Features:
    - Broker-agnostic: Works with Redis, RabbitMQ, Kafka, or in-memory
    - Async-first: Built on asyncio/anyio for non-blocking operation
    - Multiple patterns: Fire-and-forget, acknowledgment, request-response
    - Type-safe: Full type hints with Pydantic validation

Quick Start:
    >>> import asyncio
    >>> from amb_core import MessageBus, Message
    >>>
    >>> async def main():
    ...     async with MessageBus() as bus:
    ...         await bus.publish("agent.thoughts", {"thought": "Hello!"})
    >>>
    >>> asyncio.run(main())

For more information, see: https://github.com/imran-siddique/amb
"""

from __future__ import annotations

__version__ = "0.1.0"
__author__ = "Imran Siddique"
__license__ = "MIT"

from amb_core.broker import BrokerAdapter, MessageHandler
from amb_core.bus import MessageBus
from amb_core.memory_broker import InMemoryBroker
from amb_core.models import Message, MessagePriority

__all__ = [
    # Version info
    "__version__",
    "__author__",
    "__license__",
    # Core classes
    "Message",
    "MessagePriority",
    "MessageBus",
    # Broker interface
    "BrokerAdapter",
    "MessageHandler",
    "InMemoryBroker",
]
