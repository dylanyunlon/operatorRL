"""
Fiddler LoL Decoder — Live Client Data API response decoding.

Parses raw HTTP response bodies from Riot's Live Client Data API
(https://127.0.0.1:2999/liveclientdata/*) into normalised game-state
dicts consumable by the LoL agent pipeline.

Location: extensions/fiddler_bridge/src/fiddler_lol_decoder.py

Reference (拿来主义):
  - Akagi/mitm/bridge/majsoul/liqi.py: protobuf decode dispatch by endpoint
  - Seraphine/app/lol/connector.py: endpoint → handler mapping
  - Seraphine/app/lol/opgg.py: OpggDataParser per-endpoint decode
  - leagueoflegends-optimizer/articles/article5.md: Live Client Data schema
  - extensions/fiddler-bridge/src/lol_protocol_decoder.py: existing decoder stub
  - integrations/lol/src/lol_agent/live_client_connector.py: _ENDPOINTS map

Design Notes (Knuth-level critique):
  User:
    - Malformed JSON returns error dict instead of raising — never crashes caller.
    - Unknown endpoints get passthrough decoding — forward compatible.
  System:
    - Endpoint dispatcher is O(1) dict lookup, not chain of if/elif.
    - batch_decode is a thin loop — no hidden allocations.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.fiddler_bridge.fiddler_lol_decoder.v1"

# ---------------------------------------------------------------------------
# Endpoint constants (mirrors integrations/lol live_client_connector.py)
# ---------------------------------------------------------------------------

_KNOWN_ENDPOINTS = {
    "allgamedata",
    "playerlist",
    "activeplayer",
    "eventdata",
    "gamestats",
    "playerscores",
    "playeritems",
    "activeplayerabilities",
    "activeplayerrunes",
}


# ---------------------------------------------------------------------------
# Endpoint-specific parsers
# ---------------------------------------------------------------------------

class _AllGameDataParser:
    """Parse /liveclientdata/allgamedata response.

    Reference: Seraphine OpggDataParser static methods.
    """

    @staticmethod
    def parse(body: Dict[str, Any]) -> Dict[str, Any]:
        game_data = body.get("gameData", {})
        all_players = body.get("allPlayers", [])
        active = body.get("activePlayer", {})
        events = body.get("events", body.get("Events", {}).get("Events", []))

        game_time = game_data.get("gameTime", 0.0)

        players_parsed: List[Dict[str, Any]] = []
        for p in all_players:
            players_parsed.append({
                "summoner": p.get("summonerName", p.get("riotIdGameName", "")),
                "champion": p.get("championName", ""),
                "level": p.get("level", 0),
                "team": p.get("team", ""),
                "is_dead": p.get("isDead", False),
                "position": p.get("position", ""),
                "scores": p.get("scores", {}),
                "items": p.get("items", []),
            })

        return {
            "endpoint": "allgamedata",
            "game_time": game_time,
            "game_mode": game_data.get("gameMode", ""),
            "map_name": game_data.get("mapName", ""),
            "map_number": game_data.get("mapNumber", 0),
            "map_terrain": game_data.get("mapTerrain", ""),
            "players": players_parsed,
            "player_count": len(players_parsed),
            "active_player": {
                "summoner": active.get("summonerName", ""),
                "level": active.get("level", 0),
                "gold": active.get("currentGold", 0),
                "abilities": active.get("abilities", {}),
            },
            "events": events if isinstance(events, list) else [],
        }


class _PlayerListParser:
    """Parse /liveclientdata/playerlist response."""

    @staticmethod
    def parse(body: Any) -> Dict[str, Any]:
        if not isinstance(body, list):
            body = []
        players: List[Dict[str, Any]] = []
        for p in body:
            players.append({
                "summoner": p.get("summonerName", p.get("riotIdGameName", "")),
                "champion": p.get("championName", ""),
                "level": p.get("level", 0),
                "team": p.get("team", ""),
                "is_dead": p.get("isDead", False),
                "position": p.get("position", ""),
                "scores": p.get("scores", {}),
                "items": p.get("items", []),
                "skin_id": p.get("skinID", 0),
                "summoner_spells": p.get("summonerSpells", {}),
                "runes": p.get("runes", {}),
            })
        return {"endpoint": "playerlist", "players": players, "player_count": len(players)}


class _EventDataParser:
    """Parse /liveclientdata/eventdata response."""

    @staticmethod
    def parse(body: Dict[str, Any]) -> Dict[str, Any]:
        raw_events = body.get("Events", body.get("events", []))
        events: List[Dict[str, Any]] = []
        for ev in raw_events:
            events.append({
                "name": ev.get("EventName", ev.get("eventName", "")),
                "time": ev.get("EventTime", ev.get("eventTime", 0.0)),
                "event_id": ev.get("EventID", ev.get("eventID", 0)),
                "data": {k: v for k, v in ev.items()
                         if k not in ("EventName", "EventTime", "EventID",
                                      "eventName", "eventTime", "eventID")},
            })
        return {"endpoint": "eventdata", "events": events, "event_count": len(events)}


class _ActivePlayerParser:
    """Parse /liveclientdata/activeplayer response."""

    @staticmethod
    def parse(body: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "endpoint": "activeplayer",
            "summoner": body.get("summonerName", body.get("riotIdGameName", "")),
            "level": body.get("level", 0),
            "gold": body.get("currentGold", 0),
            "xp": body.get("xp", 0),
            "abilities": body.get("abilities", {}),
            "champion_stats": body.get("championStats", {}),
            "full_runes": body.get("fullRunes", {}),
        }


class _GameStatsParser:
    """Parse /liveclientdata/gamestats response."""

    @staticmethod
    def parse(body: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "endpoint": "gamestats",
            "game_time": body.get("gameTime", 0.0),
            "game_mode": body.get("gameMode", ""),
            "map_name": body.get("mapName", ""),
            "map_number": body.get("mapNumber", 0),
            "map_terrain": body.get("mapTerrain", ""),
        }


class _GenericParser:
    """Passthrough parser for unknown / unhandled endpoints."""

    @staticmethod
    def parse(body: Any, endpoint: str) -> Dict[str, Any]:
        return {"endpoint": endpoint, "raw": body}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_PARSERS: Dict[str, Any] = {
    "allgamedata": _AllGameDataParser,
    "playerlist": _PlayerListParser,
    "eventdata": _EventDataParser,
    "activeplayer": _ActivePlayerParser,
    "gamestats": _GameStatsParser,
}


# ===========================================================================
# Main class
# ===========================================================================

class FiddlerLoLDecoder:
    """Decode Fiddler-captured Live Client Data API responses.

    Usage:
        decoder = FiddlerLoLDecoder()
        result = decoder.decode({"url": "/liveclientdata/allgamedata", "body": "{...}"})

    The *url* field is used to identify the endpoint; the *body* field
    is a JSON string or a pre-parsed dict.

    Attributes:
        decode_count: Number of packets decoded so far.
        evolution_callback: Optional callback for self-evolution events.

    Reference (拿来主义):
        - Akagi liqi.py decode dispatch by message type
        - Seraphine OpggDataParser: static per-endpoint parsers
    """

    def __init__(self) -> None:
        self._decode_count: int = 0
        self._error_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def decode_count(self) -> int:
        return self._decode_count

    @property
    def error_count(self) -> int:
        return self._error_count

    # ------------------------------------------------------------------
    # Endpoint extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_endpoint(url: str) -> str:
        """Extract the endpoint name from a URL path.

        Examples:
            /liveclientdata/allgamedata  →  allgamedata
            /liveclientdata/playerlist   →  playerlist
            https://127.0.0.1:2999/liveclientdata/activeplayer → activeplayer
        """
        # strip query string
        path = url.split("?")[0]
        # find last path segment
        parts = [p for p in path.split("/") if p]
        if not parts:
            return "unknown"
        return parts[-1].lower()

    # ------------------------------------------------------------------
    # Body parsing helper
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_body(body: Any) -> Any:
        """Parse *body* from JSON string to Python object if necessary."""
        if isinstance(body, (dict, list)):
            return body
        if isinstance(body, str):
            return json.loads(body)
        if isinstance(body, bytes):
            return json.loads(body.decode("utf-8"))
        raise TypeError(f"Unsupported body type: {type(body)}")

    # ------------------------------------------------------------------
    # Core decode
    # ------------------------------------------------------------------

    def decode(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Decode a single Fiddler-captured packet.

        Args:
            raw: Dict with at least ``url`` and ``body`` keys.

        Returns:
            Normalised game-state dict with ``endpoint`` key.
            On parse errors returns ``{"error": True, ...}``.
        """
        self._decode_count += 1
        url = raw.get("url", "")
        endpoint = self._extract_endpoint(url)

        try:
            body = self._parse_body(raw.get("body", "{}"))
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
            self._error_count += 1
            err_result = {
                "endpoint": endpoint,
                "error": True,
                "error_type": type(exc).__name__,
                "error_msg": str(exc),
            }
            self._fire_evolution({"action": "decode_error", "endpoint": endpoint})
            return err_result

        parser_cls = _PARSERS.get(endpoint)
        if parser_cls is not None:
            result = parser_cls.parse(body)
        else:
            result = _GenericParser.parse(body, endpoint)

        self._fire_evolution({"action": "decode", "endpoint": endpoint})
        return result

    # ------------------------------------------------------------------
    # Batch decode
    # ------------------------------------------------------------------

    def batch_decode(self, packets: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Decode a batch of packets.

        Args:
            packets: Iterable of raw packet dicts.

        Returns:
            List of decoded results, one per input packet.
        """
        return [self.decode(pkt) for pkt in packets]

    # ------------------------------------------------------------------
    # Statistics / introspection
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "decode_count": self._decode_count,
            "error_count": self._error_count,
            "error_rate": (self._error_count / self._decode_count) if self._decode_count > 0 else 0.0,
        }

    def describe(self) -> Dict[str, Any]:
        return {
            "component": _EVOLUTION_KEY,
            "known_endpoints": sorted(_KNOWN_ENDPOINTS),
            "stats": self.get_stats(),
        }

    # ------------------------------------------------------------------
    # Evolution integration
    # ------------------------------------------------------------------

    def _fire_evolution(self, event: Dict[str, Any]) -> None:
        event.setdefault("component", _EVOLUTION_KEY)
        event.setdefault("ts", time.time())
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb(event)
            except Exception:
                logger.exception("evolution_callback raised in FiddlerLoLDecoder")

    # ------------------------------------------------------------------
    # repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return f"FiddlerLoLDecoder(decoded={self._decode_count}, errors={self._error_count})"


# ---------------------------------------------------------------------------
# Module-level convenience singleton
# ---------------------------------------------------------------------------

default_decoder: FiddlerLoLDecoder = FiddlerLoLDecoder()
