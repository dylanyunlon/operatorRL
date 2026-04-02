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
