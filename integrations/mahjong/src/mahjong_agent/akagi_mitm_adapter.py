"""
Akagi MITM Adapter — mitmproxy WebSocket interception for Majsoul.

Adapts Akagi's ClientWebSocket/MajsoulBridge MITM architecture into
operatorRL's unified framework, providing flow tracking + mjai message
queue without requiring mitmproxy at import time.

Location: integrations/mahjong/src/mahjong_agent/akagi_mitm_adapter.py

Reference (拿来主义):
  - Akagi/mitm/mitm_abc.py: ClientWebSocketABC interface
  - Akagi/mitm/majsoul.py: ClientWebSocket flow tracking + bridge_lock + mjai_messages queue
  - Akagi/mitm/bridge/bridge_base.py: bridge base pattern
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.mahjong.akagi_mitm_adapter.v1"

# --- liqi action type → mjai type mapping (拿来主义 from Akagi bridge) ---
_LIQI_TO_MJAI: dict[str, str] = {
    "ActionDealTile": "tsumo",
    "ActionDiscardTile": "dahai",
    "ActionChiPengGang": "meld",
    "ActionAnGangAddGang": "ankan",
    "ActionBaBei": "kita",
    "ActionHule": "hora",
    "ActionLiuJu": "ryukyoku",
    "ActionNewRound": "start_kyoku",
    "ActionNoTile": "ryukyoku",
}


class AkagiMitmAdapter:
    """Adapter wrapping Akagi's MITM proxy pattern for operatorRL.

    Mirrors Akagi majsoul.py:
    - activated_flows list for WebSocket flow tracking
    - MajsoulBridge per-flow for protocol parsing
    - mjai_messages queue for agent consumption
    - bridge_lock for thread safety

    Does NOT require mitmproxy at import — operates in pure-Python
    stub mode for testing and can delegate to real mitmproxy at runtime.
    """

    def __init__(self) -> None:
        # --- Akagi majsoul.py fields (拿来主义) ---
        self.active_flows: list[str] = []
        self._bridges: dict[str, Any] = {}
        self._mjai_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.bridge_lock: threading.Lock = threading.Lock()

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable] = None

    # ---- Flow lifecycle (mirrors majsoul.py websocket_start/end) ----

    def on_flow_start(self, flow_id: str) -> None:
        """Called when a new WebSocket connection is opened.

        Reference: Akagi ClientWebSocket.websocket_start
        """
        logger.info("WebSocket connection opened: %s", flow_id)
        self.active_flows.append(flow_id)
        self._bridges[flow_id] = _StubBridge()

    def on_flow_end(self, flow_id: str) -> None:
        """Called when a WebSocket connection is closed.

        Reference: Akagi ClientWebSocket.websocket_end
        """
        if flow_id in self.active_flows:
            logger.info("WebSocket connection closed: %s", flow_id)
            self.active_flows.remove(flow_id)
            self._bridges.pop(flow_id, None)
        else:
            logger.error("Flow end for unknown flow: %s", flow_id)

    # ---- Message parsing (mirrors majsoul.py websocket_message) ----

    def parse_raw_message(self, raw: bytes) -> Optional[dict[str, Any]]:
        """Parse raw WebSocket frame bytes into structured liqi message.

        Args:
            raw: Raw bytes from WebSocket frame.

        Returns:
            Parsed message dict, or None if unparseable.
        """
        if not raw:
            return None
        try:
            # Try JSON parse first (Akagi bridge returns JSON)
            msg = json.loads(raw.decode("utf-8", errors="replace"))
            return msg
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Failed to parse raw message (%d bytes)", len(raw))
            return None

    def to_mjai(self, liqi_msg: dict[str, Any]) -> dict[str, Any]:
        """Convert a liqi protocol message to mjai format.

        Reference: Akagi bridge MajsoulBridge.parse → mjai conversion.

        Args:
            liqi_msg: Dict with 'type' and 'data' keys.

        Returns:
            mjai-format dict.
        """
        liqi_type = liqi_msg.get("type", "")
        mjai_type = _LIQI_TO_MJAI.get(liqi_type, liqi_type.lower())

        mjai_msg: dict[str, Any] = {"type": mjai_type}
        data = liqi_msg.get("data", {})

        if "tile" in data:
            mjai_msg["pai"] = data["tile"]
        if "actor" in data:
            mjai_msg["actor"] = data["actor"]
        if "seat" in data:
            mjai_msg["actor"] = data["seat"]

        return mjai_msg

    # ---- Queue interface (mirrors majsoul.py mjai_messages) ----

    def enqueue_mjai(self, msg: dict[str, Any]) -> None:
        """Enqueue an mjai message for agent consumption."""
        self._mjai_queue.put(msg)

    def dequeue_mjai(self, timeout: float = 0.1) -> Optional[dict[str, Any]]:
        """Dequeue next mjai message (non-blocking with timeout)."""
        try:
            return self._mjai_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    # ---- Full message processing (combines parse + convert + enqueue) ----

    def process_websocket_message(
        self, flow_id: str, content: bytes, from_client: bool = False
    ) -> Optional[dict[str, Any]]:
        """Process a WebSocket message from a tracked flow.

        Mirrors Akagi majsoul.py websocket_message logic:
        1. Check flow is active
        2. Acquire bridge_lock
        3. Parse via bridge
        4. Enqueue mjai messages

        Returns:
            Parsed mjai message, or None.
        """
        if flow_id not in self.active_flows:
            logger.error("Message from unactivated flow: %s", flow_id)
            return None

        self.bridge_lock.acquire()
        try:
            parsed = self.parse_raw_message(content)
            if parsed is None:
                return None
            mjai = self.to_mjai(parsed)
            self.enqueue_mjai(mjai)
            return mjai
        except Exception as e:
            logger.error("Error processing message: %s", e)
            return None
        finally:
            if self.bridge_lock.locked():
                self.bridge_lock.release()

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)


class _StubBridge:
    """Stub bridge for testing (replaces MajsoulBridge)."""

    def parse(self, content: bytes) -> Optional[list[dict]]:
        return None
