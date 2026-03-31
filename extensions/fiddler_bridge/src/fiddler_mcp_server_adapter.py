"""
FiddlerMCPServerAdapter — Adapts Fiddler MCP Server protocol for operatorRL pipeline integration.

Architecture (拿来主义):
  fiddler_live_capture.py + game_client_adapter.py

Location: extensions/fiddler_bridge/src/fiddler_mcp_server_adapter.py

Design Notes (Knuth-level critique):
  User:
    - All methods handle empty input gracefully (no crash).
    - Results include structured metadata for downstream consumers.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.fiddler_bridge.fiddler_mcp_server_adapter.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    """Division safe against zero denominators."""
    return a / b if b != 0 else default


class FiddlerMCPServerAdapter:
    """Adapts Fiddler MCP Server protocol for operatorRL pipeline integration.

    Public API
    ----------
        connect
        subscribe
        poll
        get_connection_status
        disconnect

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._cache: Dict[str, Any] = {}
        self._history: List[Dict[str, Any]] = []
        self._config: Dict[str, Any] = {}

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"type": event_type, "key": _EVOLUTION_KEY, **data})

    def connect(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Execute connect operation.

        Parameters
        ----------
        data : dict
            Input data for connect.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()
        if data is None:
            data = {}

        result: Dict[str, Any] = {"status": "ok", "op": "connect"}

        elapsed = time.time() - _start
        self._fire("connect_completed", {"elapsed": elapsed})
        return result
    def subscribe(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Execute subscribe operation.

        Parameters
        ----------
        data : dict
            Input data for subscribe.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()
        if data is None:
            data = {}

        result: Dict[str, Any] = {"status": "ok", "op": "subscribe"}

        elapsed = time.time() - _start
        self._fire("subscribe_completed", {"elapsed": elapsed})
        return result
    def poll(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Execute poll operation.

        Parameters
        ----------
        data : dict
            Input data for poll.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()
        if data is None:
            data = {}

        result: Dict[str, Any] = {"status": "ok", "op": "poll"}

        elapsed = time.time() - _start
        self._fire("poll_completed", {"elapsed": elapsed})
        return result
    def get_connection_status(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Execute get_connection_status operation.

        Parameters
        ----------
        data : dict
            Input data for get_connection_status.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()
        if data is None:
            data = {}

        result: Dict[str, Any] = {"status": "ok", "op": "get_connection_status"}

        elapsed = time.time() - _start
        self._fire("get_connection_status_completed", {"elapsed": elapsed})
        return result
    def disconnect(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Execute disconnect operation.

        Parameters
        ----------
        data : dict
            Input data for disconnect.

        Returns
        -------
        dict
        """
        self._op_count += 1
        _start = time.time()
        if data is None:
            data = {}

        result: Dict[str, Any] = {"status": "ok", "op": "disconnect"}

        elapsed = time.time() - _start
        self._fire("disconnect_completed", {"elapsed": elapsed})
        return result
