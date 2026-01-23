"""In-memory broker adapter for testing and simple use cases."""

import uuid
import asyncio
from typing import Dict, List, Optional, Set
from collections import defaultdict
from amb_core.broker import BrokerAdapter, MessageHandler
from amb_core.models import Message


class InMemoryBroker(BrokerAdapter):
    """
    In-memory broker implementation for testing and simple use cases.
    
    This broker stores messages in memory and uses anyio for async handling.
    It's suitable for testing, development, and single-process applications.
    """
    
    def __init__(self):
        """Initialize the in-memory broker."""
        self._connected = False
        self._subscriptions: Dict[str, Dict[str, MessageHandler]] = defaultdict(dict)
        self._pending_messages: Dict[str, List[Message]] = defaultdict(list)
        self._response_queues: Dict[str, asyncio.Queue] = {}
        self._tasks: Set[asyncio.Task] = set()
    
    async def connect(self) -> None:
        """Establish connection (no-op for in-memory broker)."""
        self._connected = True
    
    async def disconnect(self) -> None:
        """Close connection and cancel all tasks."""
        self._connected = False
        
        # Cancel all running tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
        
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        
        self._tasks.clear()
        self._subscriptions.clear()
        self._pending_messages.clear()
        self._response_queues.clear()
    
    async def publish(self, message: Message, wait_for_confirmation: bool = False) -> Optional[str]:
        """
        Publish a message to all subscribers of the topic.
        
        Args:
            message: The message to publish
            wait_for_confirmation: If True, wait for handlers to process
        
        Returns:
            Message ID
        """
        if not self._connected:
            raise ConnectionError("Broker not connected")
        
        topic = message.topic
        
        # Store message in pending queue
        self._pending_messages[topic].append(message)
        
        # Deliver to subscribers
        handlers = self._subscriptions.get(topic, {})
        
        if wait_for_confirmation:
            # Wait for all handlers to complete
            tasks = []
            for handler in handlers.values():
                tasks.append(handler(message))
            
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        else:
            # Fire and forget - schedule handlers without waiting
            for handler in handlers.values():
                task = asyncio.create_task(handler(message))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        
        # Handle request-response pattern
        if message.correlation_id and message.correlation_id in self._response_queues:
            await self._response_queues[message.correlation_id].put(message)
        
        return message.id
    
    async def subscribe(self, topic: str, handler: MessageHandler) -> str:
        """
        Subscribe to a topic.
        
        Args:
            topic: Topic to subscribe to
            handler: Message handler function
        
        Returns:
            Subscription ID
        """
        if not self._connected:
            raise ConnectionError("Broker not connected")
        
        subscription_id = str(uuid.uuid4())
        self._subscriptions[topic][subscription_id] = handler
        return subscription_id
    
    async def unsubscribe(self, subscription_id: str) -> None:
        """
        Unsubscribe from a topic.
        
        Args:
            subscription_id: The subscription ID
        """
        for topic_handlers in self._subscriptions.values():
            if subscription_id in topic_handlers:
                del topic_handlers[subscription_id]
                return
    
    async def request(self, message: Message, timeout: float = 30.0) -> Message:
        """
        Send a request and wait for response.
        
        Args:
            message: Request message
            timeout: Timeout in seconds
        
        Returns:
            Response message
        
        Raises:
            TimeoutError: If timeout exceeded
        """
        if not self._connected:
            raise ConnectionError("Broker not connected")
        
        # Generate correlation ID if not present
        if not message.correlation_id:
            message.correlation_id = str(uuid.uuid4())
        
        # Set up reply queue
        reply_queue: asyncio.Queue = asyncio.Queue()
        self._response_queues[message.correlation_id] = reply_queue
        
        try:
            # Publish the request
            await self.publish(message, wait_for_confirmation=False)
            
            # Wait for response
            try:
                response = await asyncio.wait_for(reply_queue.get(), timeout=timeout)
                return response
            except asyncio.TimeoutError:
                raise TimeoutError(f"No response received within {timeout} seconds")
        
        finally:
            # Clean up
            if message.correlation_id in self._response_queues:
                del self._response_queues[message.correlation_id]
    
    async def get_pending_messages(self, topic: str, limit: int = 10) -> List[Message]:
        """
        Get pending messages from a topic.
        
        Args:
            topic: Topic to get messages from
            limit: Maximum number of messages
        
        Returns:
            List of messages
        """
        if not self._connected:
            raise ConnectionError("Broker not connected")
        
        messages = self._pending_messages.get(topic, [])
        return messages[-limit:] if len(messages) > limit else messages
