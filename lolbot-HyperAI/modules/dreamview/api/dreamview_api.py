"""
DreamviewAPI — REST API for monitoring dashboard and introspection.
=====================================================================

Provides an HTTP server that exposes system state, component health,
prediction history, and live logs for the monitoring dashboard.
Equivalent to Apollo's Dreamview web interface which visualizes
autonomous driving state in real-time.

Architecture position:
    modules/dreamview/api/dreamview_api.py   ← YOU ARE HERE
    ├─ Reads: all /lol/* channels via CyberNode readers
    ├─ Reads: CyberScheduler health reports
    ├─ Reads: LogCollector for live log streaming
    ├─ Serves: HTTP REST API on configurable port
    └─ Consumed by: browser dashboard, CLI tools

Apollo reference:
    modules/dreamview/backend/ — REST + WebSocket server
    modules/dreamview/proto/  — data exchange format

Design notes:
    - Lightweight HTTP server using http.server (stdlib)
    - JSON API endpoints for each module
    - Server-Sent Events (SSE) for live log streaming
    - No external dependencies (runs alongside the main pipeline)
    - Thread-safe: runs in a daemon thread, reads shared state
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

from cyber.node.node import CyberNode, Reader
from cyber.logger.cyber_logger import get_collector, get_logger, LogEntry
from modules.common.adapters.game_messages import (
    GameSnapshot,
    StrategyAdvice,
    TeamfightPrediction,
    WinPrediction,
)

logger = get_logger("dreamview")

# ─── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_PORT = 8800
_DEFAULT_HOST = "127.0.0.1"


class DreamviewConfig:
    """Dreamview server configuration."""
    def __init__(
        self,
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
        enable_cors: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.enable_cors = enable_cors


class DreamviewState:
    """Shared state container read by the HTTP handler.

    Periodically updated by the DreamviewAPI's reader thread.
    Thread-safe via copy-on-read semantics.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._game_state: Optional[Dict[str, Any]] = None
        self._win_prediction: Optional[Dict[str, Any]] = None
        self._teamfight_prediction: Optional[Dict[str, Any]] = None
        self._strategy_advice: Optional[Dict[str, Any]] = None
        self._scheduler_health: Optional[Dict[str, Any]] = None
        self._component_statuses: Dict[str, Dict[str, Any]] = {}
        self._last_update: float = 0.0

    def update_game_state(self, snapshot: GameSnapshot) -> None:
        with self._lock:
            self._game_state = snapshot.to_feature_dict()
            self._game_state["phase"] = snapshot.phase.name
            self._game_state["game_mode"] = snapshot.game_mode
            self._game_state["sequence"] = snapshot.sequence
            self._game_state["player_count"] = snapshot.player_count
            self._last_update = time.time()

    def update_win_prediction(self, pred: WinPrediction) -> None:
        with self._lock:
            self._win_prediction = {
                "blue_win_prob": pred.blue_win_prob,
                "red_win_prob": pred.red_win_prob,
                "confidence": pred.confidence,
                "model_version": pred.model_version,
                "game_time": pred.game_time,
                "predicted_winner": pred.predicted_winner.name,
                "top_features": list(pred.top_features),
            }

    def update_teamfight(self, pred: TeamfightPrediction) -> None:
        with self._lock:
            self._teamfight_prediction = {
                "likelihood": pred.likelihood,
                "blue_win_if_fight": pred.blue_win_if_fight,
                "recommended_action": pred.recommended_action,
                "reasoning": pred.reasoning,
                "game_time": pred.game_time,
            }

    def update_strategy(self, advice: StrategyAdvice) -> None:
        with self._lock:
            self._strategy_advice = {
                "primary_action": advice.primary_action,
                "secondary_action": advice.secondary_action,
                "macro_call": advice.macro_call,
                "confidence": advice.confidence,
                "urgency": advice.urgency,
                "game_time": advice.game_time,
            }

    def update_scheduler_health(self, health: Dict[str, Any]) -> None:
        with self._lock:
            self._scheduler_health = health

    def update_component_status(
        self, name: str, status: Dict[str, Any]
    ) -> None:
        with self._lock:
            self._component_statuses[name] = status

    def get_all(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "timestamp": time.time(),
                "last_update": self._last_update,
                "game_state": self._game_state,
                "win_prediction": self._win_prediction,
                "teamfight_prediction": self._teamfight_prediction,
                "strategy_advice": self._strategy_advice,
                "scheduler_health": self._scheduler_health,
                "components": dict(self._component_statuses),
            }

    def get_game_state(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._game_state

    def get_predictions(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "win": self._win_prediction,
                "teamfight": self._teamfight_prediction,
            }


# ─── HTTP Request Handler ───────────────────────────────────────────────────

def _make_handler(
    state: DreamviewState,
    enable_cors: bool,
) -> type:
    """Factory function to create handler class with captured state."""

    class DreamviewHandler(BaseHTTPRequestHandler):
        """HTTP request handler for the Dreamview API."""

        # Suppress default logging
        def log_message(self, format, *args):
            pass

        def _send_json(self, data: Any, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False, default=str)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if enable_cors:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def _send_sse_headers(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            if enable_cors:
                self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

        def do_OPTIONS(self):
            self.send_response(204)
            if enable_cors:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            params = parse_qs(parsed.query)

            routes = {
                "": self._handle_index,
                "/api/status": self._handle_status,
                "/api/game": self._handle_game_state,
                "/api/predictions": self._handle_predictions,
                "/api/strategy": self._handle_strategy,
                "/api/health": self._handle_health,
                "/api/logs": self._handle_logs,
                "/api/logs/stream": self._handle_log_stream,
            }

            handler = routes.get(path)
            if handler:
                try:
                    handler(params)
                except Exception as exc:
                    self._send_json(
                        {"error": str(exc)}, status=500
                    )
            else:
                self._send_json(
                    {"error": "Not found", "available": list(routes.keys())},
                    status=404,
                )

        def _handle_index(self, params):
            self._send_json({
                "service": "lolbot-HyperAI Dreamview",
                "version": "0.1.0",
                "endpoints": [
                    "/api/status",
                    "/api/game",
                    "/api/predictions",
                    "/api/strategy",
                    "/api/health",
                    "/api/logs",
                    "/api/logs/stream",
                ],
            })

        def _handle_status(self, params):
            self._send_json(state.get_all())

        def _handle_game_state(self, params):
            gs = state.get_game_state()
            if gs:
                self._send_json(gs)
            else:
                self._send_json({"status": "no_game"})

        def _handle_predictions(self, params):
            self._send_json(state.get_predictions())

        def _handle_strategy(self, params):
            with state._lock:
                advice = state._strategy_advice
            if advice:
                self._send_json(advice)
            else:
                self._send_json({"status": "no_advice"})

        def _handle_health(self, params):
            with state._lock:
                health = state._scheduler_health
            if health:
                self._send_json(health)
            else:
                self._send_json({"status": "unknown"})

        def _handle_logs(self, params):
            count = int(params.get("count", ["100"])[0])
            level = params.get("level", [None])[0]
            module = params.get("module", [None])[0]

            collector = get_collector()
            entries = collector.get_recent(count)

            if level:
                entries = [e for e in entries if e.level == level.upper()]
            if module:
                entries = [e for e in entries if e.module == module]

            self._send_json({
                "count": len(entries),
                "logs": [
                    {
                        "timestamp": e.timestamp,
                        "level": e.level,
                        "module": e.module,
                        "message": e.message,
                        "seq": e.seq,
                    }
                    for e in entries
                ],
            })

        def _handle_log_stream(self, params):
            """Server-Sent Events endpoint for live log streaming."""
            self._send_sse_headers()

            collector = get_collector()
            last_seq = 0

            try:
                while True:
                    entries = collector.get_since(last_seq)
                    for entry in entries:
                        data = json.dumps({
                            "level": entry.level,
                            "module": entry.module,
                            "message": entry.message,
                            "timestamp": entry.timestamp,
                        })
                        self.wfile.write(f"data: {data}\n\n".encode())
                        self.wfile.flush()
                        last_seq = entry.seq

                    time.sleep(0.5)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return DreamviewHandler


# ─── DreamviewAPI ────────────────────────────────────────────────────────────

class DreamviewAPI:
    """Dreamview monitoring dashboard API server.

    Runs an HTTP server in a background thread and maintains
    readers on all key channels to keep state up to date.

    Usage::

        api = DreamviewAPI(config, scheduler)
        api.start()
        # ... system runs ...
        api.stop()
    """

    def __init__(
        self,
        config: Optional[DreamviewConfig] = None,
    ) -> None:
        self._config = config or DreamviewConfig()
        self._state = DreamviewState()
        self._server: Optional[HTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Channel readers
        self._node: Optional[CyberNode] = None
        self._game_reader: Optional[Reader] = None
        self._win_reader: Optional[Reader] = None
        self._tf_reader: Optional[Reader] = None
        self._advice_reader: Optional[Reader] = None

    def start(self) -> bool:
        """Start the Dreamview API server.

        Returns:
            True if the server started successfully.
        """
        try:
            # Set up channel readers
            self._node = CyberNode("dreamview")
            self._game_reader = self._node.CreateReader(
                "/lol/game_state", object, pending_queue_size=4,
            )
            self._win_reader = self._node.CreateReader(
                "/lol/win_prediction", object, pending_queue_size=4,
            )
            self._tf_reader = self._node.CreateReader(
                "/lol/teamfight_prediction", object, pending_queue_size=4,
            )
            self._advice_reader = self._node.CreateReader(
                "/lol/strategy_advice", object, pending_queue_size=4,
            )

            # Start reader polling thread
            self._reader_thread = threading.Thread(
                target=self._reader_loop,
                name="dreamview-reader",
                daemon=True,
            )
            self._reader_thread.start()

            # Create and start HTTP server
            handler_class = _make_handler(
                self._state, self._config.enable_cors
            )
            self._server = HTTPServer(
                (self._config.host, self._config.port),
                handler_class,
            )
            self._server_thread = threading.Thread(
                target=self._server.serve_forever,
                name="dreamview-http",
                daemon=True,
            )
            self._server_thread.start()

            logger.info(
                "Dreamview API started at http://%s:%d",
                self._config.host, self._config.port,
            )
            return True

        except Exception as exc:
            logger.error("Failed to start Dreamview: %s", exc)
            return False

    def stop(self) -> None:
        """Stop the Dreamview API server."""
        self._stop_event.set()
        if self._server:
            self._server.shutdown()
        if self._node:
            self._node.shutdown()
        logger.info("Dreamview API stopped")

    def _reader_loop(self) -> None:
        """Periodically read channels and update shared state."""
        while not self._stop_event.is_set():
            try:
                self._game_reader.Observe()
                gs = self._game_reader.GetLatestObserved()
                if gs is not None:
                    self._state.update_game_state(gs)

                self._win_reader.Observe()
                wp = self._win_reader.GetLatestObserved()
                if wp is not None:
                    self._state.update_win_prediction(wp)

                self._tf_reader.Observe()
                tf = self._tf_reader.GetLatestObserved()
                if tf is not None:
                    self._state.update_teamfight(tf)

                self._advice_reader.Observe()
                ad = self._advice_reader.GetLatestObserved()
                if ad is not None:
                    self._state.update_strategy(ad)

            except Exception as exc:
                logger.debug("Dreamview reader error: %s", exc)

            self._stop_event.wait(timeout=0.5)

    @property
    def is_running(self) -> bool:
        return (
            self._server_thread is not None
            and self._server_thread.is_alive()
        )

    @property
    def url(self) -> str:
        return f"http://{self._config.host}:{self._config.port}"
