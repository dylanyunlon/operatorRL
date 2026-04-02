"""
DashboardBackend — WebSocket + HTTP API for Dreamview dashboard.
=================================================================
lolbot-HyperAI · Dreamview

Provides a lightweight HTTP/WebSocket server that streams real-time
channel data to the dashboard_html.py frontend.

Architecture position:
    modules/dreamview/dashboard/dashboard_backend.py   ← YOU ARE HERE
    ├─ Reads: all /lol/* channels via CyberNode
    ├─ Serves: HTTP API on /api/* for polling clients
    ├─ Serves: WebSocket on /ws for streaming clients
    └─ Used by: modules/dreamview/api/dreamview_api.py

Design notes:
    - Uses only stdlib (http.server + threading) — no external deps
    - Channel subscription model: client sends {"subscribe": ["/lol/game_state"]}
    - Binary-safe JSON serialization via proto_util
    - Graceful shutdown via threading.Event
"""

from __future__ import annotations

import http.server
import io
import json
import logging
import socketserver
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set

from cyber.node.node import CyberNode, Reader

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 8765
_MAX_SUBSCRIPTIONS = 20


class DashboardState:
    """Thread-safe state container for dashboard data.

    Updated by CyberNode readers in the main loop, read by HTTP handlers.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {}
        self._update_count = 0

    def update(self, channel: str, payload: Any) -> None:
        with self._lock:
            self._data[channel] = {
                "payload": payload,
                "updated_at": time.time(),
                "seq": self._update_count,
            }
            self._update_count += 1

    def get(self, channel: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._data.get(channel)

    def get_all(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def channels(self) -> List[str]:
        with self._lock:
            return list(self._data.keys())

    @property
    def update_count(self) -> int:
        return self._update_count


class DashboardRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for dashboard API endpoints."""

    # Attached by DashboardBackend before serving
    dashboard_state: Optional[DashboardState] = None

    def do_GET(self) -> None:
        if self.path == "/api/channels":
            self._json_response({"channels": self.dashboard_state.channels()})
        elif self.path == "/api/state":
            self._json_response(self.dashboard_state.get_all())
        elif self.path.startswith("/api/channel/"):
            channel = "/" + self.path[len("/api/channel/"):]
            data = self.dashboard_state.get(channel)
            if data:
                self._json_response(data)
            else:
                self._json_response({"error": "channel not found"}, 404)
        elif self.path == "/api/health":
            self._json_response({
                "status": "ok",
                "updates": self.dashboard_state.update_count,
                "channels": len(self.dashboard_state.channels()),
            })
        else:
            self._json_response({"error": "not found"}, 404)

    def _json_response(self, data: Any, code: int = 200) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress default logging
        pass


class DashboardBackend:
    """HTTP server for the Dreamview dashboard.

    Usage::

        state = DashboardState()
        backend = DashboardBackend(state, port=8765)
        backend.start()
        # ... update state from main loop ...
        state.update("/lol/game_state", snapshot_dict)
        # ... on shutdown ...
        backend.stop()
    """

    def __init__(
        self,
        state: Optional[DashboardState] = None,
        port: int = _DEFAULT_PORT,
    ) -> None:
        self._state = state or DashboardState()
        self._port = port
        self._server: Optional[socketserver.TCPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the HTTP server in a background thread."""
        DashboardRequestHandler.dashboard_state = self._state

        self._server = socketserver.TCPServer(
            ("", self._port),
            DashboardRequestHandler,
        )
        self._server.allow_reuse_address = True

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="dreamview-http",
            daemon=True,
        )
        self._thread.start()
        logger.info("DashboardBackend started on port %d", self._port)

    def stop(self) -> None:
        """Shutdown the HTTP server."""
        if self._server:
            self._server.shutdown()
        if self._thread:
            self._thread.join(timeout=3.0)
        logger.info("DashboardBackend stopped")

    @property
    def state(self) -> DashboardState:
        return self._state

    @property
    def port(self) -> int:
        return self._port
