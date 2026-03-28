"""
Network layer - HTTP traffic capture, analysis, and streaming.

Modules:
    fiddler_client      : Fiddler MCP server communication
    live_client_data    : LoL Live Client Data API models
    packet_analyzer     : Deep packet inspection and classification
    session_manager     : Session lifecycle and ring-buffer management
    traffic_classifier  : FSM-based lifecycle tracking with anomaly detection
    connection_pool     : Async HTTP connection pool with health checks
    websocket_bridge    : Real-time WebSocket event streaming
"""

from lol_fiddler_agent.network.fiddler_client import (
    FiddlerConfig,
    FiddlerMCPClient,
    FiddlerMCPError,
    HTTPSession,
    FilterCriteria,
)
from lol_fiddler_agent.network.packet_analyzer import (
    PacketAnalyzer,
    AnalyzedPacket,
    APIEndpointCategory,
    PacketSignature,
)
from lol_fiddler_agent.network.session_manager import (
    SessionManager,
    SessionManagerConfig,
    SessionRingBuffer,
)
from lol_fiddler_agent.network.traffic_classifier import (
    TrafficClassifier,
    TrafficPriority,
    AnomalyType,
)
from lol_fiddler_agent.network.connection_pool import (
    ConnectionPool,
    EndpointConfig,
    ManagedConnection,
)
from lol_fiddler_agent.network.websocket_bridge import (
    WebSocketBridge,
    BridgeConfig,
    MessageBroadcaster,
)

__all__ = [
    "FiddlerConfig", "FiddlerMCPClient", "FiddlerMCPError", "HTTPSession",
    "FilterCriteria", "PacketAnalyzer", "AnalyzedPacket", "APIEndpointCategory",
    "PacketSignature", "SessionManager", "SessionManagerConfig",
    "SessionRingBuffer", "TrafficClassifier", "TrafficPriority",
    "AnomalyType", "ConnectionPool", "EndpointConfig", "ManagedConnection",
    "WebSocketBridge", "BridgeConfig", "MessageBroadcaster",
]
