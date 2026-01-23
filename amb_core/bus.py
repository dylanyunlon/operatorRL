"""Main MessageBus implementation."""

import uuid
from typing import Optional, Dict, Any
from amb_core.broker import BrokerAdapter, MessageHandler
from amb_core.models import Message, MessagePriority
from amb_core.memory_broker import InMemoryBroker


class MessageBus:
    """
    Main message bus interface for AI Agents.
    
    This class provides a simple API for publishing and subscribing to messages
    with support for both "fire and forget" and "wait for verification" patterns.
    """
    
    def __init__(self, adapter: Optional[BrokerAdapter] = None):
        """
        Initialize the message bus.
        
        Args:
            adapter: Broker adapter to use. If None, uses InMemoryBroker.
        """
        self._adapter = adapter or InMemoryBroker()
        self._connected = False
    
    async def connect(self) -> None:
        """Connect to the broker."""
        await self._adapter.connect()
        self._connected = True
    
    async def disconnect(self) -> None:
        """Disconnect from the broker."""
        await self._adapter.disconnect()
        self._connected = False
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()
    
    async def publish(
        self,
        topic: str,
        payload: Dict[str, Any],
        *,
        priority: MessagePriority = MessagePriority.NORMAL,
        sender: Optional[str] = None,
        wait_for_confirmation: bool = False,
        **kwargs
    ) -> str:
        """
        Publish a message to a topic.
        
        This method supports both "fire and forget" (default) and
        "wait for verification" patterns via the wait_for_confirmation parameter.
        
        Args:
            topic: Topic to publish to
            payload: Message payload
            priority: Message priority level
            sender: Optional sender identifier
            wait_for_confirmation: If True, wait for broker confirmation
            **kwargs: Additional message attributes
        
        Returns:
            Message ID
        
        Example:
            # Fire and forget (fast, no guarantee)
            await bus.publish("agent.thoughts", {"thought": "Hello"})
            
            # Wait for verification (slower, with guarantee)
            msg_id = await bus.publish(
                "agent.action",
                {"action": "execute"},
                wait_for_confirmation=True
            )
        """
        if not self._connected:
            raise ConnectionError("Message bus not connected")
        
        message = Message(
            id=str(uuid.uuid4()),
            topic=topic,
            payload=payload,
            priority=priority,
            sender=sender,
            **kwargs
        )
        
        await self._adapter.publish(message, wait_for_confirmation=wait_for_confirmation)
        return message.id
    
    async def subscribe(self, topic: str, handler: MessageHandler) -> str:
        """
        Subscribe to a topic with a message handler.
        
        Args:
            topic: Topic to subscribe to
            handler: Async function to handle messages
        
        Returns:
            Subscription ID
        
        Example:
            async def handle_message(msg: Message):
                print(f"Received: {msg.payload}")
            
            sub_id = await bus.subscribe("agent.thoughts", handle_message)
        """
        if not self._connected:
            raise ConnectionError("Message bus not connected")
        
        return await self._adapter.subscribe(topic, handler)
    
    async def unsubscribe(self, subscription_id: str) -> None:
        """
        Unsubscribe from a topic.
        
        Args:
            subscription_id: Subscription ID to unsubscribe
        """
        if not self._connected:
            raise ConnectionError("Message bus not connected")
        
        await self._adapter.unsubscribe(subscription_id)
    
    async def request(
        self,
        topic: str,
        payload: Dict[str, Any],
        *,
        timeout: float = 30.0,
        sender: Optional[str] = None,
        **kwargs
    ) -> Message:
        """
        Send a request and wait for a response.
        
        This implements the request-response pattern for cases where
        you need to wait for a reply from another agent.
        
        Args:
            topic: Topic to send request to
            payload: Request payload
            timeout: Maximum time to wait for response
            sender: Optional sender identifier
            **kwargs: Additional message attributes
        
        Returns:
            Response message
        
        Raises:
            TimeoutError: If no response within timeout
        
        Example:
            response = await bus.request(
                "agent.query",
                {"query": "What is the status?"},
                timeout=10.0
            )
            print(response.payload)
        """
        if not self._connected:
            raise ConnectionError("Message bus not connected")
        
        message = Message(
            id=str(uuid.uuid4()),
            topic=topic,
            payload=payload,
            sender=sender,
            correlation_id=str(uuid.uuid4()),
            **kwargs
        )
        
        return await self._adapter.request(message, timeout=timeout)
    
    async def reply(self, original_message: Message, payload: Dict[str, Any]) -> str:
        """
        Reply to a request message.
        
        Args:
            original_message: The original request message
            payload: Reply payload
        
        Returns:
            Reply message ID
        
        Example:
            async def handle_request(msg: Message):
                result = process_request(msg.payload)
                await bus.reply(msg, {"result": result})
        """
        if not self._connected:
            raise ConnectionError("Message bus not connected")
        
        if not original_message.correlation_id:
            raise ValueError("Original message has no correlation_id")
        
        reply_topic = original_message.reply_to or original_message.topic
        
        reply_message = Message(
            id=str(uuid.uuid4()),
            topic=reply_topic,
            payload=payload,
            correlation_id=original_message.correlation_id,
            sender=None,
        )
        
        await self._adapter.publish(reply_message, wait_for_confirmation=False)
        return reply_message.id
