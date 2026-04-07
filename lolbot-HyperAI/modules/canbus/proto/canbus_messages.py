"""
CAN Bus layer message types.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

class ConnectionState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECONNECTING = auto()
    ERROR = auto()

class CaptureSource(Enum):
    LCU_LIVE_CLIENT = "lcu_live_client"
    LCU_WEBSOCKET = "lcu_websocket"
    FIDDLER_PROXY = "fiddler_proxy"
    REPLAY_FILE = "replay_file"
    MOCK = "mock"

@dataclass(frozen=True)
class CanbusFrame:
    source: CaptureSource
    endpoint: str
    payload: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    sequence: int = 0
    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source.value,
            "endpoint": self.endpoint,
            "timestamp": round(self.timestamp, 3),
            "sequence": self.sequence,
            "latency_ms": round(self.latency_ms, 1),
            "payload_size": len(str(self.payload)),
        }

@dataclass(frozen=True)
class ConnectionStatus:
    state: ConnectionState
    source: CaptureSource
    connected_at: float = 0.0
    error_message: str = ""
    reconnect_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.name,
            "source": self.source.value,
            "connected_at": self.connected_at,
            "error": self.error_message,
            "reconnects": self.reconnect_count,
        }

@dataclass(frozen=True)
class LCUEndpointData:
    endpoint: str
    status_code: int
    data: Dict[str, Any]
    latency_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)
    content_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "status": self.status_code,
            "latency_ms": round(self.latency_ms, 1),
            "hash": self.content_hash[:8],
        }


# ═══════════════════════════════════════════════════════════════════════════
# Claude20: Complete canbus proto definitions with validation
# ═══════════════════════════════════════════════════════════════════════════

import hashlib
import math
from typing import Callable, List, Set

_REQUIRED_ALLGAMEDATA_KEYS: Set[str] = {"allPlayers", "gameData"}
_REQUIRED_GAMEDATA_KEYS: Set[str] = {"gameTime", "gameMode"}
_REQUIRED_PLAYER_KEYS: Set[str] = {"summonerName", "championName", "team", "level"}
_MAX_PAYLOAD_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB sanity limit
_MAX_PLAYERS = 10  # 5v5


class CanbusValidationError(ValueError):
    """Raised when canbus message validation fails."""
    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"Canbus validation: {field} — {reason}")


class CanbusFrameValidator:
    """Validates CanbusFrame payloads before publishing.

    Apollo reference: modules/canbus/canbus_component.cc validates
    chassis message fields before writing to the chassis channel.

    Usage::
        validator = CanbusFrameValidator()
        errors = validator.validate_allgamedata(payload)
        if errors:
            reject_frame()
    """

    def validate_allgamedata(self, payload: Dict[str, Any]) -> List[CanbusValidationError]:
        """Validate a /liveclientdata/allgamedata response."""
        errors: List[CanbusValidationError] = []

        if not isinstance(payload, dict):
            errors.append(CanbusValidationError("payload", "Not a dict"))
            return errors

        # Required top-level keys
        for key in _REQUIRED_ALLGAMEDATA_KEYS:
            if key not in payload:
                errors.append(CanbusValidationError(key, "Missing required key"))

        # gameData validation
        game_data = payload.get("gameData", {})
        if isinstance(game_data, dict):
            for key in _REQUIRED_GAMEDATA_KEYS:
                if key not in game_data:
                    errors.append(CanbusValidationError(
                        f"gameData.{key}", "Missing required field"))
            game_time = game_data.get("gameTime", -1)
            if isinstance(game_time, (int, float)):
                if game_time < 0:
                    errors.append(CanbusValidationError(
                        "gameData.gameTime", f"Negative value: {game_time}"))
                if game_time > 7200:  # 2 hours
                    errors.append(CanbusValidationError(
                        "gameData.gameTime", f"Implausibly large: {game_time}"))

        # allPlayers validation
        players = payload.get("allPlayers", [])
        if isinstance(players, list):
            if len(players) == 0:
                errors.append(CanbusValidationError(
                    "allPlayers", "Empty player list"))
            elif len(players) > _MAX_PLAYERS:
                errors.append(CanbusValidationError(
                    "allPlayers", f"Too many players: {len(players)}"))
            else:
                for i, player in enumerate(players):
                    if isinstance(player, dict):
                        for key in _REQUIRED_PLAYER_KEYS:
                            if key not in player:
                                errors.append(CanbusValidationError(
                                    f"allPlayers[{i}].{key}", "Missing field"))
        else:
            errors.append(CanbusValidationError(
                "allPlayers", f"Not a list: {type(players).__name__}"))

        return errors

    def validate_frame(self, frame: CanbusFrame) -> List[CanbusValidationError]:
        """Validate a CanbusFrame message."""
        errors: List[CanbusValidationError] = []
        if frame.sequence < 0:
            errors.append(CanbusValidationError("sequence", "Negative"))
        if frame.latency_ms < 0:
            errors.append(CanbusValidationError("latency_ms", "Negative"))
        if frame.timestamp <= 0:
            errors.append(CanbusValidationError("timestamp", "Non-positive"))
        payload_str = str(frame.payload)
        if len(payload_str) > _MAX_PAYLOAD_SIZE_BYTES:
            errors.append(CanbusValidationError(
                "payload", f"Too large: {len(payload_str)} bytes"))
        return errors


@dataclass(frozen=True)
class FiddlerCaptureFrame:
    """A single Fiddler network capture frame.

    Claude20: Typed wrapper for raw Fiddler JSON sessions.
    """
    session_id: str = ""
    request_url: str = ""
    request_method: str = "GET"
    response_status: int = 0
    response_body: Dict[str, Any] = field(default_factory=dict)
    response_size_bytes: int = 0
    timestamp: float = field(default_factory=time.time)
    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "url": self.request_url,
            "method": self.request_method,
            "status": self.response_status,
            "size": self.response_size_bytes,
            "latency_ms": round(self.latency_ms, 1),
            "timestamp": round(self.timestamp, 3),
        }

    @classmethod
    def from_fiddler_session(cls, session: Dict[str, Any]) -> "FiddlerCaptureFrame":
        """Create from a raw Fiddler session dict."""
        return cls(
            session_id=str(session.get("id", "")),
            request_url=session.get("request_url", session.get("url", "")),
            request_method=session.get("request_method", session.get("method", "GET")),
            response_status=int(session.get("response_status", session.get("status", 0))),
            response_body=session.get("response_body", session.get("body", {})),
            response_size_bytes=int(session.get("response_size", 0)),
            latency_ms=float(session.get("latency_ms", 0.0)),
        )


@dataclass(frozen=True)
class CanbusHealthReport:
    """Aggregated canbus health status.

    Claude20: Published periodically for MonitorComponent consumption.
    """
    connection_state: str = "DISCONNECTED"
    data_source: str = "unknown"
    game_active: bool = False
    game_time: float = 0.0
    poll_rate_hz: float = 0.0
    avg_latency_ms: float = 0.0
    error_rate: float = 0.0
    backoff_s: float = 0.0
    stale_count: int = 0
    total_polls: int = 0
    total_errors: int = 0
    fiddler_enabled: bool = False
    fiddler_message_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "connection": self.connection_state,
            "source": self.data_source,
            "game_active": self.game_active,
            "game_time": round(self.game_time, 1),
            "poll_rate_hz": round(self.poll_rate_hz, 1),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "error_rate": round(self.error_rate, 4),
            "backoff_s": round(self.backoff_s, 1),
            "total_polls": self.total_polls,
            "total_errors": self.total_errors,
        }

    @property
    def is_healthy(self) -> bool:
        return (
            self.connection_state == "CONNECTED"
            and self.error_rate < 0.1
            and self.stale_count < 50
        )


def compute_content_hash(allgamedata: Dict[str, Any]) -> str:
    """Compute a fast content hash for dedup.

    Uses game_time + event_count + player_gold_sum as key.
    Much faster than full JSON hash while catching meaningful changes.

    Claude20: Improved hash that also catches gold changes
    (the old hash only used game_time + event_count).
    """
    game_data = allgamedata.get("gameData", {})
    game_time = game_data.get("gameTime", 0)
    events = allgamedata.get("events", {}).get("Events", [])
    event_count = len(events)

    # Include aggregate gold for better change detection
    players = allgamedata.get("allPlayers", [])
    gold_sum = 0
    for p in players:
        score = p.get("scores", {})
        gold_sum += score.get("currentGold", 0)

    key = f"{game_time:.1f}:{event_count}:{gold_sum}"
    return hashlib.md5(key.encode()).hexdigest()[:16]


def estimate_payload_staleness(
    payload_game_time: float,
    expected_game_time: float,
    tolerance_s: float = 5.0,
) -> float:
    """Estimate how stale a payload is relative to expected game time.

    Returns staleness in seconds. Negative means payload is from the future
    (clock skew). Values > tolerance_s indicate stale data.

    Claude20: Used by SensorFusion to rank data source freshness.
    """
    return expected_game_time - payload_game_time
