"""
WebSocket Bridge - Real-time event streaming from game state to consumers.

Provides a local WebSocket server that broadcasts game state updates
and strategic advice to connected clients (overlay UI, mobile app, etc.).

Wire protocol:
    All messages are JSON objects with a "type" field:
      - "game_state"   : full LiveGameState snapshot
      - "advice"        : StrategicAdvice from the strategy engine
      - "event"         : in-game event (kill, dragon, etc.)
      - "lifecycle"     : game lifecycle transition
      - "heartbeat"     : keepalive with timestamp
      - "subscribe"     : client → server channel subscription
      - "unsubscribe"   : client → server channel removal
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Set
from weakref import WeakSet

logger = logging.getLogger(__name__)


class MessageType(str, Enum):
    """WebSocket message types."""
    GAME_STATE = "game_state"
    ADVICE = "advice"
    EVENT = "event"
    LIFECYCLE = "lifecycle"
    HEARTBEAT = "heartbeat"
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    ERROR = "error"
    ACK = "ack"


class Channel(str, Enum):
    """Subscription channels for selective data delivery."""
    ALL = "all"
    GAME_STATE = "game_state"
    ADVICE = "advice"
    EVENTS = "events"
    LIFECYCLE = "lifecycle"
    METRICS = "metrics"


@dataclass
class WSMessage:
    """A structured WebSocket message."""
    msg_type: MessageType
    payload: dict[str, Any]
    channel: Channel = Channel.ALL
    timestamp: float = field(default_factory=time.time)
    sequence: int = 0

    def to_json(self) -> str:
        return json.dumps({
            "type": self.msg_type.value,
            "channel": self.channel.value,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "seq": self.sequence,
        }, default=str)

    @classmethod
    def heartbeat(cls, seq: int = 0) -> "WSMessage":
        return cls(
            msg_type=MessageType.HEARTBEAT,
            payload={"server_time": time.time()},
            sequence=seq,
        )

    @classmethod
    def error(cls, message: str, code: int = 400) -> "WSMessage":
        return cls(
            msg_type=MessageType.ERROR,
            payload={"message": message, "code": code},
        )


@dataclass
class ClientConnection:
    """Represents a connected WebSocket client."""
    client_id: str
    connected_at: float = field(default_factory=time.time)
    subscriptions: Set[Channel] = field(default_factory=lambda: {Channel.ALL})
    messages_sent: int = 0
    messages_received: int = 0
    last_activity: float = field(default_factory=time.time)

    # The actual send coroutine (injected by transport layer)
    _send_fn: Optional[Any] = field(default=None, repr=False)

    def is_subscribed(self, channel: Channel) -> bool:
        return Channel.ALL in self.subscriptions or channel in self.subscriptions

    def subscribe(self, channel: Channel) -> None:
        self.subscriptions.add(channel)

    def unsubscribe(self, channel: Channel) -> None:
        self.subscriptions.discard(channel)

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.connected_at

    async def send(self, message: WSMessage) -> bool:
        """Send a message to this client."""
        if not self._send_fn:
            return False
        try:
            await self._send_fn(message.to_json())
            self.messages_sent += 1
            self.last_activity = time.time()
            return True
        except Exception as e:
            logger.warning("Send to %s failed: %s", self.client_id, e)
            return False


@dataclass
class BridgeConfig:
    """WebSocket bridge configuration."""
    host: str = "127.0.0.1"
    port: int = 9876
    heartbeat_interval: float = 10.0
    max_clients: int = 16
    message_queue_size: int = 256
    enable_compression: bool = True


class MessageBroadcaster:
    """Fan-out broadcaster for WebSocket messages.

    Maintains a set of connected clients and broadcasts messages
    to clients subscribed to the relevant channel.
    Drops messages for disconnected clients without blocking.
    """

    def __init__(self, max_clients: int = 16) -> None:
        self._clients: dict[str, ClientConnection] = {}
        self._max_clients = max_clients
        self._sequence = 0
        self._total_broadcast = 0
        self._total_dropped = 0

    def add_client(self, client: ClientConnection) -> bool:
        """Register a new client connection."""
        if len(self._clients) >= self._max_clients:
            logger.warning("Max clients reached (%d), rejecting", self._max_clients)
            return False
        self._clients[client.client_id] = client
        logger.info("Client connected: %s (total: %d)", client.client_id, len(self._clients))
        return True

    def remove_client(self, client_id: str) -> bool:
        """Remove a client connection."""
        if client_id in self._clients:
            del self._clients[client_id]
            logger.info("Client disconnected: %s (total: %d)", client_id, len(self._clients))
            return True
        return False

    def get_client(self, client_id: str) -> Optional[ClientConnection]:
        return self._clients.get(client_id)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def connected_ids(self) -> list[str]:
        return list(self._clients.keys())

    async def broadcast(self, message: WSMessage) -> int:
        """Broadcast a message to all subscribed clients.

        Returns:
            Number of clients that received the message.
        """
        self._sequence += 1
        message.sequence = self._sequence
        self._total_broadcast += 1

        sent_count = 0
        failed_ids: list[str] = []

        for client_id, client in self._clients.items():
            if not client.is_subscribed(message.channel):
                continue
            success = await client.send(message)
            if success:
                sent_count += 1
            else:
                failed_ids.append(client_id)
                self._total_dropped += 1

        # Clean up failed connections
        for cid in failed_ids:
            self.remove_client(cid)

        return sent_count

    async def send_to(self, client_id: str, message: WSMessage) -> bool:
        """Send a message to a specific client."""
        client = self._clients.get(client_id)
        if not client:
            return False
        return await client.send(message)

    def get_stats(self) -> dict[str, Any]:
        return {
            "connected_clients": len(self._clients),
            "total_broadcast": self._total_broadcast,
            "total_dropped": self._total_dropped,
            "sequence": self._sequence,
        }


class WebSocketBridge:
    """WebSocket server bridge for real-time game data streaming.

    This is a transport-agnostic bridge. The actual WebSocket
    server implementation (aiohttp, websockets, etc.) is plugged
    in via the ``start_server`` method or by subclassing.

    Example::

        bridge = WebSocketBridge(BridgeConfig())

        # Publish game state update
        await bridge.publish_game_state(game_state_dict)

        # Publish strategic advice
        await bridge.publish_advice(advice_dict)
    """

    def __init__(self, config: Optional[BridgeConfig] = None) -> None:
        self._config = config or BridgeConfig()
        self._broadcaster = MessageBroadcaster(max_clients=self._config.max_clients)
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._message_queue: asyncio.Queue[WSMessage] = asyncio.Queue(
            maxsize=self._config.message_queue_size,
        )
        self._dispatch_task: Optional[asyncio.Task] = None

    # ── Public Publish API ────────────────────────────────────────────────

    async def publish_game_state(self, state: dict[str, Any]) -> int:
        """Broadcast a game state snapshot."""
        msg = WSMessage(
            msg_type=MessageType.GAME_STATE,
            payload=state,
            channel=Channel.GAME_STATE,
        )
        return await self._broadcaster.broadcast(msg)

    async def publish_advice(self, advice: dict[str, Any]) -> int:
        """Broadcast strategic advice."""
        msg = WSMessage(
            msg_type=MessageType.ADVICE,
            payload=advice,
            channel=Channel.ADVICE,
        )
        return await self._broadcaster.broadcast(msg)

    async def publish_event(self, event: dict[str, Any]) -> int:
        """Broadcast a game event."""
        msg = WSMessage(
            msg_type=MessageType.EVENT,
            payload=event,
            channel=Channel.EVENTS,
        )
        return await self._broadcaster.broadcast(msg)

    async def publish_lifecycle(self, old_phase: str, new_phase: str) -> int:
        """Broadcast a lifecycle transition."""
        msg = WSMessage(
            msg_type=MessageType.LIFECYCLE,
            payload={"old_phase": old_phase, "new_phase": new_phase},
            channel=Channel.LIFECYCLE,
        )
        return await self._broadcaster.broadcast(msg)

    # ── Client Management ─────────────────────────────────────────────────

    def register_client(self, client: ClientConnection) -> bool:
        return self._broadcaster.add_client(client)

    def unregister_client(self, client_id: str) -> bool:
        return self._broadcaster.remove_client(client_id)

    async def handle_client_message(self, client_id: str, raw: str) -> None:
        """Handle an incoming message from a client."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await self._broadcaster.send_to(
                client_id,
                WSMessage.error("Invalid JSON"),
            )
            return

        msg_type = data.get("type", "")
        client = self._broadcaster.get_client(client_id)
        if not client:
            return

        client.messages_received += 1

        if msg_type == MessageType.SUBSCRIBE.value:
            channel_name = data.get("channel", "all")
            try:
                channel = Channel(channel_name)
                client.subscribe(channel)
                await self._broadcaster.send_to(
                    client_id,
                    WSMessage(msg_type=MessageType.ACK, payload={"subscribed": channel_name}),
                )
            except ValueError:
                await self._broadcaster.send_to(
                    client_id,
                    WSMessage.error(f"Unknown channel: {channel_name}"),
                )

        elif msg_type == MessageType.UNSUBSCRIBE.value:
            channel_name = data.get("channel", "")
            try:
                channel = Channel(channel_name)
                client.unsubscribe(channel)
                await self._broadcaster.send_to(
                    client_id,
                    WSMessage(msg_type=MessageType.ACK, payload={"unsubscribed": channel_name}),
                )
            except ValueError:
                pass

    # ── Server Lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the heartbeat and dispatch loops."""
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())
        logger.info("WebSocket bridge started on %s:%d", self._config.host, self._config.port)

    async def stop(self) -> None:
        """Stop all background tasks."""
        self._running = False
        for task in (self._heartbeat_task, self._dispatch_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("WebSocket bridge stopped")

    async def _heartbeat_loop(self) -> None:
        seq = 0
        while self._running:
            await asyncio.sleep(self._config.heartbeat_interval)
            seq += 1
            await self._broadcaster.broadcast(WSMessage.heartbeat(seq))

    async def _dispatch_loop(self) -> None:
        """Dispatch queued messages."""
        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self._message_queue.get(), timeout=1.0,
                )
                await self._broadcaster.broadcast(msg)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def enqueue(self, message: WSMessage) -> bool:
        """Add a message to the broadcast queue (non-blocking)."""
        try:
            self._message_queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            logger.warning("Message queue full, dropping message")
            return False

    # ── Introspection ─────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    def get_stats(self) -> dict[str, Any]:
        return {
            **self._broadcaster.get_stats(),
            "queue_size": self._message_queue.qsize(),
            "running": self._running,
        }
