"""AMB Core - A lightweight, broker-agnostic message bus for AI Agents."""

__version__ = "0.1.0"

from amb_core.models import Message, MessagePriority
from amb_core.bus import MessageBus
from amb_core.broker import BrokerAdapter, MessageHandler

__all__ = [
    "Message",
    "MessagePriority",
    "MessageBus",
    "BrokerAdapter",
    "MessageHandler",
]
