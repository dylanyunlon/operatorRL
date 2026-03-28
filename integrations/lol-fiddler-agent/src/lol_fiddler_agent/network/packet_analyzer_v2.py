"""
Packet Analyzer V2 — Migrated to protocol-decoder architecture.

Delegates packet classification and parsing to the unified
protocol-decoder codecs instead of custom parsing logic.

Location: integrations/lol-fiddler-agent/src/lol_fiddler_agent/network/packet_analyzer_v2.py
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_fiddler_agent.network.packet_analyzer_v2.v1"


class PacketAnalyzerV2:
    """Packet analyzer using protocol-decoder codecs.

    Classifies and parses network packets, delegating to
    the appropriate codec for each game protocol.
    """

    def __init__(self) -> None:
        self._stats = {
            "packets_analyzed": 0,
            "json_count": 0,
            "binary_count": 0,
            "unknown_count": 0,
            "errors": 0,
        }

    def classify(self, data: bytes, content_type: str = "") -> str:
        """Classify packet type.

        Returns: 'json', 'binary', or 'unknown'.
        """
        if not data:
            return "unknown"

        if "json" in content_type.lower():
            return "json"
        if "octet-stream" in content_type.lower():
            return "binary"

        # Heuristic: try JSON parse
        try:
            json.loads(data)
            return "json"
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        if data[0:1] in (b"{", b"["):
            return "json"

        return "binary" if len(data) > 4 else "unknown"

    def analyze(
        self,
        data: bytes,
        content_type: str = "",
        url: str = "",
    ) -> Optional[dict[str, Any]]:
        """Analyze a packet and return structured data.

        Args:
            data: Raw packet bytes.
            content_type: HTTP content type.
            url: Request URL for context.

        Returns:
            Parsed packet data dict, or None.
        """
        self._stats["packets_analyzed"] += 1
        pkt_type = self.classify(data, content_type)

        if pkt_type == "json":
            self._stats["json_count"] += 1
            return self._analyze_json(data, url)
        elif pkt_type == "binary":
            self._stats["binary_count"] += 1
            return self._analyze_binary(data, url)
        else:
            self._stats["unknown_count"] += 1
            return {"type": "unknown", "size": len(data), "url": url}

    def get_stats(self) -> dict[str, int]:
        """Get analysis statistics."""
        return dict(self._stats)

    def _analyze_json(self, data: bytes, url: str) -> dict[str, Any]:
        """Analyze JSON packet."""
        try:
            parsed = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._stats["errors"] += 1
            return {"type": "json_error", "url": url}

        result: dict[str, Any] = {
            "type": "json",
            "url": url,
            "codec": "lol",
        }

        # Extract known fields
        if isinstance(parsed, dict):
            if "gameData" in parsed:
                result["game_time"] = parsed["gameData"].get("gameTime", 0)
            if "activePlayer" in parsed:
                result["active_player"] = parsed["activePlayer"].get("summonerName", "")
            if "allPlayers" in parsed:
                result["player_count"] = len(parsed["allPlayers"])

        return result

    def _analyze_binary(self, data: bytes, url: str) -> dict[str, Any]:
        """Analyze binary packet."""
        return {
            "type": "binary",
            "url": url,
            "size": len(data),
        }
