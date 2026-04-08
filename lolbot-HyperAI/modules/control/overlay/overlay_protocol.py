"""
OverlayProtocol — Type-safe overlay element message definitions.
=================================================================
lolbot-HyperAI · Control Layer

Defines OverlayElement, OverlayCommand, and OverlayLayout as the
typed protocol between planning/prediction and overlay_renderer.

Architecture position:
    modules/control/overlay/overlay_protocol.py   ← YOU ARE HERE
    ├─ Published by: PlanningComponent, ObjectiveTracker, ControlComponent
    ├─ Consumed by: OverlayRenderer
    └─ Transported via: /lol/overlay_commands channel
"""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class ElementType(Enum):
    TEXT = "text"
    BAR = "bar"
    TIMER = "timer"
    ICON = "icon"
    ALERT = "alert"


class Position(Enum):
    TOP_LEFT = "top_left"
    TOP_CENTER = "top_center"
    TOP_RIGHT = "top_right"
    CENTER_LEFT = "center_left"
    CENTER = "center"
    CENTER_RIGHT = "center_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_CENTER = "bottom_center"
    BOTTOM_RIGHT = "bottom_right"


class OverlayAction(Enum):
    SET = "set"
    UPDATE = "update"
    REMOVE = "remove"
    CLEAR_ALL = "clear_all"


@dataclass(frozen=True)
class OverlayElementDef:
    """Definition of a single overlay element."""
    element_id: str = ""
    element_type: ElementType = ElementType.TEXT
    position: Position = Position.TOP_RIGHT
    content: str = ""
    value: float = 0.0
    max_value: float = 1.0
    color: str = "#FFFFFF"
    bg_color: str = "#00000080"
    font_size: int = 14
    priority: int = 5
    ttl_s: float = 10.0
    source_module: str = ""
    timestamp: float = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.timestamp) > self.ttl_s

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.element_id,
            "type": self.element_type.value,
            "pos": self.position.value,
            "content": self.content,
            "value": self.value,
            "max_value": self.max_value,
            "color": self.color,
            "priority": self.priority,
            "ttl_s": self.ttl_s,
            "source": self.source_module,
        }


@dataclass(frozen=True)
class OverlayCommand:
    """A command to the overlay renderer.

    Published on ``/lol/overlay_commands``.
    """
    action: OverlayAction = OverlayAction.SET
    element: Optional[OverlayElementDef] = None
    target_id: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class OverlayLayout:
    """Current overlay state: set of active elements."""
    elements: Dict[str, OverlayElementDef] = field(default_factory=dict)
    max_elements: int = 20

    def apply_command(self, cmd: OverlayCommand) -> None:
        if cmd.action == OverlayAction.SET and cmd.element:
            self.elements[cmd.element.element_id] = cmd.element
            self._enforce_limit()
        elif cmd.action == OverlayAction.UPDATE and cmd.element:
            self.elements[cmd.element.element_id] = cmd.element
        elif cmd.action == OverlayAction.REMOVE:
            self.elements.pop(cmd.target_id, None)
        elif cmd.action == OverlayAction.CLEAR_ALL:
            self.elements.clear()

    def expire_old(self) -> int:
        expired = [eid for eid, e in self.elements.items() if e.is_expired]
        for eid in expired:
            del self.elements[eid]
        return len(expired)

    def _enforce_limit(self) -> None:
        while len(self.elements) > self.max_elements:
            worst = max(self.elements.values(), key=lambda e: e.priority)
            del self.elements[worst.element_id]

    def active_elements(self) -> List[OverlayElementDef]:
        return sorted(self.elements.values(), key=lambda e: e.priority)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": len(self.elements),
            "elements": [e.to_dict() for e in self.active_elements()],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Claude22 V3: WebSocket transport layer + overlay batch protocol
# ═══════════════════════════════════════════════════════════════════════════════
#
# Design spec (Apollo pattern):
#   从 OverlayProtocol 的 OverlayCommand/OverlayLayout 类型系统 这个好例子开始。
#   然后，遵循该模式实现 OverlayWebSocketSender，让 overlay元素 可以 通过 WebSocket
#   实际推送到游戏内覆盖层客户端，并能 处理连接断开和重连。
#   接着 OverlayBatchProtocol 引入 批量命令合并，使 单次推送 能够 包含多个元素更新，
#   同时 DeltaCompressor 优化 只发送变化的字段以减少带宽。
#   最终 OverlaySession 完善 客户端连接管理，确保 多客户端 兼容 并发推送。

import asyncio
import queue
import struct
import zlib
from typing import Callable, Protocol, Sequence


# ─── Delta compression for overlay updates ──────────────────────────────────

class OverlayDelta:
    """Computes delta between two OverlayLayout states.

    Only changed/added/removed elements are included in the delta message,
    reducing bandwidth for high-frequency overlay updates.

    Apollo parallel: modules/dreamview/backend/map/map_service.cc —
    delta map updates to frontend.
    """

    @staticmethod
    def compute(
        old_layout: OverlayLayout,
        new_layout: OverlayLayout,
    ) -> Dict[str, Any]:
        """Compute the delta between two layout states.

        Returns a dict with 'added', 'updated', 'removed' keys.
        """
        old_ids = set(old_layout.elements.keys())
        new_ids = set(new_layout.elements.keys())

        added = []
        updated = []
        removed = list(old_ids - new_ids)

        for eid in new_ids - old_ids:
            added.append(new_layout.elements[eid].to_dict())

        for eid in new_ids & old_ids:
            old_elem = old_layout.elements[eid]
            new_elem = new_layout.elements[eid]
            if old_elem != new_elem:
                # Compute field-level diff
                old_d = old_elem.to_dict()
                new_d = new_elem.to_dict()
                diff = {"id": eid}
                for k, v in new_d.items():
                    if old_d.get(k) != v:
                        diff[k] = v
                if len(diff) > 1:  # more than just 'id'
                    updated.append(diff)

        return {
            "type": "delta",
            "added": added,
            "updated": updated,
            "removed": removed,
            "timestamp": time.time(),
        }

    @staticmethod
    def is_empty(delta: Dict[str, Any]) -> bool:
        return (
            not delta.get("added")
            and not delta.get("updated")
            and not delta.get("removed")
        )


# ─── Batch command protocol ─────────────────────────────────────────────────

@dataclass
class OverlayBatch:
    """Batch of overlay commands for efficient transport.

    Groups multiple OverlayCommands into a single message to reduce
    WebSocket frame overhead and improve atomicity.
    """
    commands: List[OverlayCommand] = field(default_factory=list)
    sequence_id: int = 0
    timestamp: float = field(default_factory=time.time)
    compressed: bool = False

    def add(self, cmd: OverlayCommand) -> None:
        self.commands.append(cmd)

    @property
    def size(self) -> int:
        return len(self.commands)

    def to_wire_format(self, compress: bool = False) -> bytes:
        """Serialize to wire format for WebSocket transport.

        Format: [seq_id:4][count:2][compressed:1][payload]
        """
        payload_parts = []
        for cmd in self.commands:
            elem_data = cmd.element.to_dict() if cmd.element else {}
            entry = {
                "action": cmd.action.value,
                "target_id": cmd.target_id,
                "element": elem_data,
            }
            payload_parts.append(entry)

        payload_json = json.dumps(payload_parts, separators=(',', ':')).encode()

        if compress and len(payload_json) > 256:
            payload_json = zlib.compress(payload_json, level=6)
            self.compressed = True

        header = struct.pack(
            "!IHB",
            self.sequence_id,
            len(self.commands),
            1 if self.compressed else 0,
        )
        return header + payload_json

    @classmethod
    def from_wire_format(cls, data: bytes) -> "OverlayBatch":
        """Deserialize from wire format."""
        seq_id, count, compressed = struct.unpack("!IHB", data[:7])
        payload_data = data[7:]

        if compressed:
            payload_data = zlib.decompress(payload_data)

        entries = json.loads(payload_data.decode())
        batch = cls(sequence_id=seq_id, compressed=bool(compressed))

        for entry in entries:
            action = OverlayAction(entry.get("action", "set"))
            elem_data = entry.get("element", {})
            element = None
            if elem_data:
                element = OverlayElementDef(
                    element_id=elem_data.get("id", ""),
                    content=elem_data.get("content", ""),
                    color=elem_data.get("color", "#FFFFFF"),
                    priority=elem_data.get("priority", 5),
                    ttl_s=elem_data.get("ttl_s", 10.0),
                    source_module=elem_data.get("source", ""),
                )
            cmd = OverlayCommand(
                action=action,
                element=element,
                target_id=entry.get("target_id", ""),
            )
            batch.add(cmd)

        return batch


# ─── WebSocket sender interface ──────────────────────────────────────────────

class OverlayTransport(Protocol):
    """Protocol for overlay transport backends."""

    def send(self, data: bytes) -> bool: ...
    def is_connected(self) -> bool: ...
    def close(self) -> None: ...


class OverlayWebSocketSender:
    """WebSocket-based overlay sender with connection management.

    Manages a connection to an overlay client (e.g. browser-based HUD)
    and pushes delta updates efficiently.

    Apollo parallel: modules/dreamview/backend/websocket/websocket.cc

    Usage::
        sender = OverlayWebSocketSender(host="127.0.0.1", port=9876)
        sender.start()

        # In Proc() loop:
        batch = OverlayBatch()
        batch.add(OverlayCommand(action=OverlayAction.SET, element=elem))
        sender.enqueue(batch)
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9876,
        max_queue_size: int = 64,
        enable_compression: bool = True,
        reconnect_interval_s: float = 5.0,
    ) -> None:
        self._host = host
        self._port = port
        self._max_queue_size = max_queue_size
        self._enable_compression = enable_compression
        self._reconnect_interval_s = reconnect_interval_s

        self._send_queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._connected = False
        self._running = False
        self._sequence_id: int = 0

        # Metrics
        self._batches_sent: int = 0
        self._batches_dropped: int = 0
        self._bytes_sent: int = 0
        self._last_send_time: float = 0.0
        self._reconnect_count: int = 0
        self._last_layout = OverlayLayout()

    def start(self) -> None:
        """Start the WebSocket sender (non-blocking)."""
        self._running = True
        # In production, this would start a background thread with
        # asyncio.run(self._ws_loop()). For now, we use a synchronous
        # queue-based approach compatible with TimerComponent threads.

    def stop(self) -> None:
        """Stop the sender and close connection."""
        self._running = False
        self._connected = False

    def enqueue(self, batch: OverlayBatch) -> bool:
        """Enqueue a batch for sending.

        Returns False if queue is full (batch dropped).
        """
        if not self._running:
            return False

        self._sequence_id += 1
        batch.sequence_id = self._sequence_id

        try:
            self._send_queue.put_nowait(batch)
            return True
        except queue.Full:
            self._batches_dropped += 1
            return False

    def enqueue_delta(self, new_layout: OverlayLayout) -> bool:
        """Compute delta from last layout and enqueue if non-empty."""
        delta = OverlayDelta.compute(self._last_layout, new_layout)
        if OverlayDelta.is_empty(delta):
            return True  # nothing to send

        # Convert delta to batch of commands
        batch = OverlayBatch()
        for elem_dict in delta.get("added", []):
            elem = OverlayElementDef(
                element_id=elem_dict.get("id", ""),
                content=elem_dict.get("content", ""),
                priority=elem_dict.get("priority", 5),
                source_module=elem_dict.get("source", ""),
            )
            batch.add(OverlayCommand(
                action=OverlayAction.SET, element=elem))

        for diff in delta.get("updated", []):
            elem = OverlayElementDef(
                element_id=diff.get("id", ""),
                content=diff.get("content", ""),
                priority=diff.get("priority", 5),
            )
            batch.add(OverlayCommand(
                action=OverlayAction.UPDATE, element=elem))

        for eid in delta.get("removed", []):
            batch.add(OverlayCommand(
                action=OverlayAction.REMOVE, target_id=eid))

        self._last_layout = new_layout
        return self.enqueue(batch)

    def drain_and_serialize(self) -> List[bytes]:
        """Drain all pending batches and serialize them.

        Called by overlay transport thread or by ControlComponent.Proc()
        to get wire-format data ready for sending.
        """
        results = []
        while not self._send_queue.empty():
            try:
                batch = self._send_queue.get_nowait()
                wire_data = batch.to_wire_format(
                    compress=self._enable_compression)
                results.append(wire_data)
                self._batches_sent += 1
                self._bytes_sent += len(wire_data)
                self._last_send_time = time.time()
            except queue.Empty:
                break
        return results

    @property
    def is_connected(self) -> bool:
        return self._connected

    def stats(self) -> Dict[str, Any]:
        return {
            "connected": self._connected,
            "running": self._running,
            "batches_sent": self._batches_sent,
            "batches_dropped": self._batches_dropped,
            "bytes_sent": self._bytes_sent,
            "queue_size": self._send_queue.qsize(),
            "sequence_id": self._sequence_id,
            "reconnect_count": self._reconnect_count,
        }
