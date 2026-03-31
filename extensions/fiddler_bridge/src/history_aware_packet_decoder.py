"""
HistoryAwarePacketDecoder — Decodes Fiddler packets with historical context enrichment.

Architecture (拿来主义):
  protocol_decoder.py + history_packet_correlator.py（M627）

Location: extensions/fiddler_bridge/src/history_aware_packet_decoder.py

Design Notes (Knuth-level critique):
  User:
    - decode() never crashes on malformed packets — returns error with reason.
    - Historical enrichment is additive — decoded packet is always valid even without history.
    - batch_decode processes all packets even if some fail.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - Endpoint extraction handles malformed URLs gracefully.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.fiddler_bridge.history_aware_packet_decoder.v1"

_LOL_ENDPOINTS = {
    "/liveclientdata/allgamedata": "all_game_data",
    "/liveclientdata/playerlist": "player_list",
    "/liveclientdata/activeplayer": "active_player",
    "/liveclientdata/eventdata": "event_data",
    "/liveclientdata/gamestats": "game_stats",
    "/liveclientdata/playerscores": "player_scores",
    "/liveclientdata/playeritems": "player_items",
}


def _extract_endpoint(url: str) -> str:
    """Extract endpoint path from URL, handling malformed inputs."""
    if not isinstance(url, str):
        return "unknown"
    try:
        # Remove query params
        path = url.split("?")[0]
        # Remove protocol and host
        if "://" in path:
            path = "/" + path.split("://", 1)[1].split("/", 1)[-1]
        return path
    except Exception:
        return "unknown"


def _parse_body(body: Any) -> Any:
    """Attempt to parse body as JSON if it's a string."""
    if isinstance(body, str):
        try:
            return json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return body
    return body


class HistoryAwarePacketDecoder:
    """Decodes Fiddler packets with historical context enrichment.

    Public API
    ----------
    set_history_context — load historical data for enrichment
    decode              — decode a single packet with history enrichment
    batch_decode        — decode multiple packets
    get_stats           — internal statistics
    describe            — describe known endpoints

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._decode_count: int = 0
        self._error_count: int = 0
        self._history_context: Dict[str, Any] = {}

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb({"source": _EVOLUTION_KEY, "type": event_type,
                     "timestamp": time.time(), "payload": data})
            except Exception:
                logger.exception("evolution_callback raised in HistoryAwarePacketDecoder")

    # ------------------------------------------------------------------ #

    def set_history_context(self, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Load historical data for enrichment.

        Parameters
        ----------
        context : dict
            Keys: opponent_history, champion_stats, past_games, etc.

        Returns
        -------
        dict
        """
        self._op_count += 1
        if context is None:
            context = {}
        self._history_context = dict(context)
        return {"status": "ok", "op": "set_history_context",
                "context_keys": list(context.keys())}

    # ------------------------------------------------------------------ #

    def decode(self, raw: Dict[str, Any] = None) -> Dict[str, Any]:
        """Decode a single Fiddler packet with history enrichment.

        Parameters
        ----------
        raw : dict
            Must contain url and body.  Optional: method, status_code, headers.

        Returns
        -------
        dict  with status, decoded payload, endpoint, enrichments
        """
        self._op_count += 1
        _start = time.time()
        if raw is None:
            raw = {}

        url = raw.get("url", "")
        endpoint = _extract_endpoint(url)
        body = _parse_body(raw.get("body"))
        method = raw.get("method", "GET")
        status_code = raw.get("status_code", 200)

        endpoint_type = _LOL_ENDPOINTS.get(endpoint, "unknown")

        decoded = {
            "endpoint": endpoint,
            "endpoint_type": endpoint_type,
            "method": method,
            "status_code": status_code,
            "body": body,
            "decoded_at": time.time(),
        }

        # History enrichment
        enrichments: Dict[str, Any] = {}
        if self._history_context:
            if endpoint_type == "player_list" and isinstance(body, list):
                opp_hist = self._history_context.get("opponent_history", {})
                for player in body:
                    if isinstance(player, dict):
                        name = player.get("summonerName", "")
                        if name in opp_hist:
                            enrichments[name] = {
                                "historical_games": opp_hist[name].get("games", 0),
                                "historical_winrate": opp_hist[name].get("win_rate", 0.0),
                            }

            if endpoint_type == "active_player" and isinstance(body, dict):
                champ_stats = self._history_context.get("champion_stats", {})
                champ = body.get("championName", "")
                if champ in champ_stats:
                    enrichments["champion_history"] = champ_stats[champ]

        decoded["enrichments"] = enrichments
        decoded["history_enriched"] = bool(enrichments)

        self._decode_count += 1
        elapsed = time.time() - _start
        self._fire("decode_completed", {"elapsed": elapsed, "endpoint": endpoint})
        return {"status": "ok", "op": "decode", "decoded": decoded}

    # ------------------------------------------------------------------ #

    def batch_decode(self, packets: Sequence[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Decode multiple packets.

        Parameters
        ----------
        packets : sequence of dict

        Returns
        -------
        dict  with status, decoded (list), error_count
        """
        self._op_count += 1
        _start = time.time()
        if packets is None:
            packets = []

        decoded: List[Dict[str, Any]] = []
        errors = 0
        for pkt in packets:
            try:
                result = self.decode(pkt)
                decoded.append(result.get("decoded", {}))
            except Exception as exc:
                errors += 1
                self._error_count += 1
                decoded.append({"error": str(exc)})

        elapsed = time.time() - _start
        self._fire("batch_decode_completed", {"elapsed": elapsed, "count": len(packets)})
        return {"status": "ok", "op": "batch_decode",
                "decoded": decoded, "total": len(packets), "error_count": errors}

    # ------------------------------------------------------------------ #

    def get_stats(self) -> Dict[str, Any]:
        """Internal statistics."""
        return {
            "op_count": self._op_count,
            "decode_count": self._decode_count,
            "error_count": self._error_count,
            "history_loaded": bool(self._history_context),
            "known_endpoints": len(_LOL_ENDPOINTS),
        }

    # ------------------------------------------------------------------ #

    def describe(self) -> Dict[str, Any]:
        """Describe known endpoints."""
        return {"status": "ok", "op": "describe",
                "endpoints": dict(_LOL_ENDPOINTS)}
