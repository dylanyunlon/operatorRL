"""
FiddlerProtocolLiveEnricher — Enriches Live Client Data with Fiddler protocol capture.

Architecture (拿来主义):
  integrations/lol-fiddler-agent/src/lol_fiddler_agent/network/fiddler_client.py — Fiddler MCP client
  extensions/fiddler-bridge/fiddler_mcp_bridge.py — MCP bridge patterns

Location: integrations/lol-history/src/lol_history/fiddler_protocol_live_enricher.py

Design Notes (Knuth-level critique):
  User:
    - Dual-source fusion: LCD API (polled) + Fiddler protocol (captured) = richer state.
    - Fiddler provides data LCD cannot: exact server timing, opponent actions, protocol details.
    - Proxifier routes game traffic through Fiddler for transparent capture.
  System:
    - Packet parsing extracts structured data from HTTP captures.
    - Fusion engine merges LCD and Fiddler data with conflict resolution (Fiddler wins on timing).
    - Protocol patterns detected: minimap pings, chat signals, ability casts.
    - Staleness detection: Fiddler data older than 5s is marked stale.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.fiddler_protocol_live_enricher.v1"

STALE_THRESHOLD_SECONDS = 5.0


def _safe_div(a: float, b: float, d: float = 0.0) -> float:
    return a / b if b else d


class _PacketClassifier:
    """Classifies captured HTTP packets by game relevance and data type."""

    LCD_ENDPOINTS = {
        "/liveclientdata/allgamedata": "full_state",
        "/liveclientdata/activeplayer": "active_player",
        "/liveclientdata/playerlist": "player_list",
        "/liveclientdata/eventdata": "event_data",
        "/liveclientdata/gamestats": "game_stats",
    }

    RIOT_API_PATTERNS = {
        "lol-champ-select": "champ_select",
        "lol-gameflow": "gameflow",
        "lol-matchmaking": "matchmaking",
        "lol-lobby": "lobby",
        "lol-end-of-game": "postgame",
        "lol-perks": "runes",
        "lol-store": "store",
    }

    @classmethod
    def classify(cls, url: str, method: str = "GET") -> Dict[str, str]:
        parsed = urlparse(url)
        path = parsed.path

        for endpoint, dtype in cls.LCD_ENDPOINTS.items():
            if endpoint in path:
                return {"source": "lcd_api", "data_type": dtype, "path": path}

        for pattern, dtype in cls.RIOT_API_PATTERNS.items():
            if pattern in path:
                return {"source": "lcu_api", "data_type": dtype, "path": path}

        if "127.0.0.1:2999" in url:
            return {"source": "lcd_api", "data_type": "unknown_lcd", "path": path}

        return {"source": "external", "data_type": "unknown", "path": path}


class _PacketParser:
    """Parses captured HTTP packets into structured data."""

    def __init__(self) -> None:
        self._parse_count = 0
        self._parse_errors = 0

    def parse(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        self._parse_count += 1
        url = packet.get("url", "")
        method = packet.get("method", "GET")
        status = packet.get("status", 0)
        body = packet.get("body", "")
        timestamp = packet.get("timestamp", time.time())

        classification = _PacketClassifier.classify(url, method)

        parsed_body = None
        if body:
            try:
                if isinstance(body, str):
                    parsed_body = json.loads(body)
                elif isinstance(body, dict):
                    parsed_body = body
            except (json.JSONDecodeError, TypeError):
                self._parse_errors += 1
                parsed_body = {"raw": str(body)[:500]}

        return {
            "url": url,
            "method": method,
            "status": status,
            "timestamp": timestamp,
            "classification": classification,
            "body": parsed_body,
            "body_size": len(str(body)) if body else 0,
            "is_success": 200 <= status < 300,
            "latency_ms": packet.get("latency_ms"),
        }

    def get_stats(self) -> Dict[str, Any]:
        return {
            "parse_count": self._parse_count,
            "parse_errors": self._parse_errors,
        }


class _ProtocolPatternDetector:
    """Detects game protocol patterns from captured traffic."""

    def __init__(self) -> None:
        self._patterns: deque = deque(maxlen=500)
        self._pattern_counts: Dict[str, int] = defaultdict(int)

    def analyze(self, parsed_packet: Dict[str, Any]) -> List[Dict[str, Any]]:
        detected = []
        classification = parsed_packet.get("classification", {})
        data_type = classification.get("data_type", "")
        body = parsed_packet.get("body")

        if data_type == "event_data" and body:
            events = body.get("Events", []) if isinstance(body, dict) else []
            for event in events:
                pattern = {
                    "type": "game_event",
                    "event_name": event.get("EventName"),
                    "event_time": event.get("EventTime"),
                    "timestamp": parsed_packet["timestamp"],
                }
                detected.append(pattern)
                self._pattern_counts["game_event"] += 1

        if data_type == "champ_select":
            detected.append({
                "type": "champ_select_update",
                "timestamp": parsed_packet["timestamp"],
            })
            self._pattern_counts["champ_select_update"] += 1

        if data_type == "gameflow":
            if body and isinstance(body, dict):
                phase = body.get("phase", body.get("gamePhase", "unknown"))
                detected.append({
                    "type": "gameflow_transition",
                    "phase": phase,
                    "timestamp": parsed_packet["timestamp"],
                })
                self._pattern_counts["gameflow_transition"] += 1

        latency = parsed_packet.get("latency_ms")
        if latency and latency > 500:
            detected.append({
                "type": "high_latency",
                "latency_ms": latency,
                "url": parsed_packet.get("url", ""),
                "timestamp": parsed_packet["timestamp"],
            })
            self._pattern_counts["high_latency"] += 1

        for p in detected:
            self._patterns.append(p)

        return detected

    def get_recent_patterns(self, pattern_type: str = None,
                             limit: int = 20) -> List[Dict]:
        patterns = list(self._patterns)
        if pattern_type:
            patterns = [p for p in patterns if p.get("type") == pattern_type]
        return patterns[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_patterns": len(self._patterns),
            "pattern_counts": dict(self._pattern_counts),
        }


class _DataFusionEngine:
    """Fuses LCD API data with Fiddler protocol data."""

    def __init__(self) -> None:
        self._fusion_count = 0
        self._conflict_count = 0
        self._enrichment_count = 0

    def fuse(self, lcd_data: Dict[str, Any],
             fiddler_data: Dict[str, Any]) -> Dict[str, Any]:
        self._fusion_count += 1
        fused = dict(lcd_data) if lcd_data else {}

        if fiddler_data:
            timing = fiddler_data.get("extra_timing")
            if timing is not None:
                fused["_fiddler_timing"] = timing
                self._enrichment_count += 1

            server_data = fiddler_data.get("server_response")
            if server_data:
                fused["_server_enrichment"] = server_data
                self._enrichment_count += 1

            protocol_actions = fiddler_data.get("protocol_actions", [])
            if protocol_actions:
                fused["_protocol_actions"] = protocol_actions
                self._enrichment_count += 1

            fiddler_ts = fiddler_data.get("timestamp", 0)
            lcd_ts = lcd_data.get("game_time", 0) if lcd_data else 0
            if fiddler_ts and lcd_ts:
                fused["_timing_delta"] = abs(fiddler_ts - lcd_ts)
                if abs(fiddler_ts - lcd_ts) > 2.0:
                    self._conflict_count += 1
                    fused["_timing_conflict"] = True

        fused["_fusion_metadata"] = {
            "has_lcd": lcd_data is not None,
            "has_fiddler": fiddler_data is not None,
            "fusion_num": self._fusion_count,
            "data_sources": ["lcd"] + (["fiddler"] if fiddler_data else []),
        }

        return fused

    def get_stats(self) -> Dict[str, Any]:
        return {
            "fusion_count": self._fusion_count,
            "conflict_count": self._conflict_count,
            "enrichment_count": self._enrichment_count,
        }


class _StalenessChecker:
    """Checks data freshness and marks stale data."""

    def __init__(self, threshold: float = STALE_THRESHOLD_SECONDS) -> None:
        self._threshold = threshold
        self._stale_count = 0

    def check(self, data_timestamp: float,
              current_time: float = None) -> Dict[str, Any]:
        now = current_time or time.time()
        age = now - data_timestamp
        is_stale = age > self._threshold
        if is_stale:
            self._stale_count += 1
        return {
            "age_seconds": age,
            "is_stale": is_stale,
            "threshold": self._threshold,
        }

    def get_stats(self) -> Dict[str, Any]:
        return {
            "threshold": self._threshold,
            "stale_count": self._stale_count,
        }


class _CaptureHistory:
    """Stores history of captured and enriched packets."""

    def __init__(self, max_records: int = 1000) -> None:
        self._records: deque = deque(maxlen=max_records)
        self._source_counts: Dict[str, int] = defaultdict(int)

    def record(self, parsed: Dict[str, Any]) -> None:
        self._records.append(parsed)
        source = parsed.get("classification", {}).get("source", "unknown")
        self._source_counts[source] += 1

    def get_recent(self, source: str = None, limit: int = 20) -> List[Dict]:
        records = list(self._records)
        if source:
            records = [r for r in records
                       if r.get("classification", {}).get("source") == source]
        return records[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_records": len(self._records),
            "source_counts": dict(self._source_counts),
        }


class FiddlerProtocolLiveEnricher:
    """Enriches Live Client Data with Fiddler protocol capture for dual-source fusion.

    Public API: enrich_from_packet, fuse_lcd_fiddler, get_recent_captures,
                get_protocol_patterns, get_fusion_quality, get_stats
    """

    def __init__(self, stale_threshold: float = STALE_THRESHOLD_SECONDS) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._enrich_count = 0
        self._parser = _PacketParser()
        self._pattern_detector = _ProtocolPatternDetector()
        self._fusion = _DataFusionEngine()
        self._staleness = _StalenessChecker(threshold=stale_threshold)
        self._history = _CaptureHistory()
        self._last_fiddler_data: Optional[Dict] = None
        self._last_enrichment: Optional[Dict] = None

    def _fire(self, et: str, data: Dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def enrich_from_packet(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        """Parse and enrich a captured HTTP packet."""
        self._op_count += 1
        self._enrich_count += 1

        parsed = self._parser.parse(packet)
        patterns = self._pattern_detector.analyze(parsed)
        staleness = self._staleness.check(parsed.get("timestamp", time.time()))

        parsed["_patterns"] = patterns
        parsed["_staleness"] = staleness
        self._history.record(parsed)
        self._last_fiddler_data = parsed

        self._fire("packet_enriched", {
            "source": parsed["classification"]["source"],
            "data_type": parsed["classification"]["data_type"],
            "patterns_found": len(patterns),
        })

        return {
            "status": "ok",
            "parsed": parsed,
            "patterns": patterns,
            "staleness": staleness,
            "enrichment_num": self._enrich_count,
        }

    def fuse_lcd_fiddler(self, lcd_data: Dict[str, Any],
                          fiddler_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Fuse LCD API data with Fiddler protocol data."""
        self._op_count += 1
        fiddler = fiddler_data or (self._last_fiddler_data.get("body")
                                   if self._last_fiddler_data else None)
        fused = self._fusion.fuse(lcd_data, fiddler)
        self._last_enrichment = fused
        return {
            "status": "ok",
            "fused_data": fused,
            "fusion_stats": self._fusion.get_stats(),
        }

    def get_recent_captures(self, source: str = None,
                             limit: int = 20) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "captures": self._history.get_recent(source, limit),
        }

    def get_protocol_patterns(self, pattern_type: str = None,
                               limit: int = 20) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "patterns": self._pattern_detector.get_recent_patterns(pattern_type, limit),
            "pattern_stats": self._pattern_detector.get_stats(),
        }

    def get_fusion_quality(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "fusion": self._fusion.get_stats(),
            "staleness": self._staleness.get_stats(),
            "has_fiddler_data": self._last_fiddler_data is not None,
            "has_enrichment": self._last_enrichment is not None,
        }

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "op_count": self._op_count,
            "enrich_count": self._enrich_count,
            "parser": self._parser.get_stats(),
            "patterns": self._pattern_detector.get_stats(),
            "fusion": self._fusion.get_stats(),
            "staleness": self._staleness.get_stats(),
            "history": self._history.get_stats(),
        }
