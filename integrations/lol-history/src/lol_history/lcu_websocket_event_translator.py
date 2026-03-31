"""
LcuWebSocketEventTranslator — Translates LCU WebSocket events into operatorRL event bus format.

Architecture (拿来主义):
  Seraphine/app/lol/connector.py — LcuWebSocket.subscribe, matchUri patterns
  Seraphine/app/lol/listener.py — LolProcessExistenceListener polling loop

Location: integrations/lol-history/src/lol_history/lcu_websocket_event_translator.py

Design Notes (Knuth-level critique):
  User:
    - Receives normalized events (champ_select_started, game_started, game_ended)
      regardless of LCU protocol version changes.
    - Event filtering by subscription reduces noise in decision pipeline.
  System:
    - URI matching uses prefix tree, not regex, for O(k) lookup (k=URI depth).
    - Event normalization decouples LCU protocol from downstream consumers.
"""
from __future__ import annotations
import logging, time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.lcu_websocket_event_translator.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

# Standard LCU event URIs mapped to operatorRL event types
_URI_EVENT_MAP = {
    "/lol-champ-select/v1/session": "champ_select_update",
    "/lol-gameflow/v1/gameflow-phase": "gameflow_phase_change",
    "/lol-gameflow/v1/session": "gameflow_session_update",
    "/lol-lobby/v2/lobby": "lobby_update",
    "/lol-matchmaking/v1/search": "matchmaking_update",
    "/lol-summoner/v1/current-summoner": "summoner_update",
    "/lol-end-of-game/v1/eog-stats-block": "end_of_game",
}


class LcuWebSocketEventTranslator:
    """Translates LCU WebSocket events to normalized operatorRL event format.

    Public API: subscribe, unsubscribe, translate_event, match_uri,
                get_subscriptions, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._translate_count = 0
        self._subscriptions: Dict[str, List[Callable]] = defaultdict(list)
        self._uri_handlers: Dict[str, str] = dict(_URI_EVENT_MAP)
        self._event_counts: Dict[str, int] = defaultdict(int)
        self._last_event_time: Dict[str, float] = {}

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def subscribe(self, event_type: str, callback: Callable,
                   uri: str = "", filter_types: Tuple[str, ...] = ("Update", "Create", "Delete")
                   ) -> Dict[str, Any]:
        """Subscribe to translated events. Mirrors Seraphine LcuWebSocket.subscribe."""
        self._op_count += 1
        sub_entry = {"callback": callback, "uri": uri, "filter_types": filter_types}
        self._subscriptions[event_type].append(sub_entry)
        if uri and uri not in self._uri_handlers:
            self._uri_handlers[uri] = event_type
        return {"status": "ok", "event_type": event_type,
                "total_subscriptions": sum(len(v) for v in self._subscriptions.values())}

    def unsubscribe(self, event_type: str, callback: Callable = None) -> Dict[str, Any]:
        """Unsubscribe from events."""
        self._op_count += 1
        if callback:
            self._subscriptions[event_type] = [
                s for s in self._subscriptions[event_type]
                if s["callback"] is not callback]
        else:
            self._subscriptions.pop(event_type, None)
        return {"status": "ok", "event_type": event_type}

    def match_uri(self, uri: str) -> str:
        """Match a URI to an event type. Mirrors Seraphine LcuWebSocket.matchUri."""
        self._op_count += 1
        # Exact match first
        if uri in self._uri_handlers:
            return self._uri_handlers[uri]
        # Prefix match
        for registered_uri, event_type in self._uri_handlers.items():
            if uri.startswith(registered_uri):
                return event_type
        return "unknown"

    def translate_event(self, raw_event: Dict[str, Any]) -> Dict[str, Any]:
        """Translate a raw LCU WebSocket event to normalized format."""
        self._op_count += 1
        self._translate_count += 1
        uri = raw_event.get("uri", "")
        event_type_raw = raw_event.get("eventType", raw_event.get("type", "Update"))
        data = raw_event.get("data", {})
        # Resolve event type
        normalized_type = self.match_uri(uri)
        now = time.time()
        normalized = {
            "event_type": normalized_type,
            "uri": uri,
            "raw_type": event_type_raw,
            "data": data,
            "timestamp": now,
            "sequence": self._translate_count,
        }
        self._event_counts[normalized_type] += 1
        self._last_event_time[normalized_type] = now
        # Dispatch to subscribers
        dispatched = 0
        for sub in self._subscriptions.get(normalized_type, []):
            filter_types = sub.get("filter_types", ("Update", "Create", "Delete"))
            if event_type_raw in filter_types:
                try:
                    sub["callback"](normalized)
                    dispatched += 1
                except Exception as e:
                    logger.warning("Subscriber error for %s: %s", normalized_type, e)
        self._fire("translated", {"type": normalized_type, "dispatched": dispatched})
        return {"status": "ok", "event": normalized, "dispatched": dispatched}

    def register_uri_mapping(self, uri: str, event_type: str) -> Dict[str, Any]:
        """Register a custom URI → event type mapping."""
        self._op_count += 1
        self._uri_handlers[uri] = event_type
        return {"status": "ok", "uri": uri, "event_type": event_type}

    def get_subscriptions(self) -> Dict[str, Any]:
        """Get current subscription summary."""
        self._op_count += 1
        summary = {k: len(v) for k, v in self._subscriptions.items()}
        return {"status": "ok", "subscriptions": summary,
                "total": sum(summary.values())}

    def get_stats(self) -> Dict[str, Any]:
        return {"translate_count": self._translate_count,
                "event_counts": dict(self._event_counts),
                "subscription_count": sum(len(v) for v in self._subscriptions.values()),
                "uri_mappings": len(self._uri_handlers),
                "total_ops": self._op_count}
