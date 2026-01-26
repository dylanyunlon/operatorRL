"""
AMB - Agent Message Bus

Core messaging infrastructure for inter-agent communication in the Carbon Auditor Swarm.
Provides pub/sub messaging with topic-based routing.
"""

from .message_bus import MessageBus, Message, MessageType
from .topics import Topics

__all__ = ["MessageBus", "Message", "MessageType", "Topics"]
