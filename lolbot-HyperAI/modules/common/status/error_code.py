"""
Error Codes & Status — Unified status reporting across all modules.
====================================================================

Every module Proc() and every inter-module call should return or
propagate a ``Status`` object.  This mirrors Apollo's
``modules/common/status/status.h`` which unifies error reporting
across perception, prediction, planning, and canbus.

Architecture position:
    modules/common/status/error_code.py   ← YOU ARE HERE
    ├─ Used by: every module's Proc() return path
    ├─ Used by: inter-component message envelopes
    └─ Consumed by: dreamview dashboard for error display

Apollo reference:
    modules/common/status/status.h      — Status class
    modules/common_msgs/basic_msgs/error_code.proto — ErrorCode enum

Design notes:
    - Immutable Status objects (frozen dataclass)
    - Chain-of-responsibility: wrap inner errors with context
    - ErrorCode enum mirrors Apollo's categorization
    - JSON-serializable for log ingestion
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum, unique
from typing import Any, Dict, List, Optional


@unique
class ErrorCode(IntEnum):
    """Unified error codes across all lolbot-HyperAI modules.

    Numeric ranges:
        0         — OK
        1000-1999 — Canbus (LCU/Fiddler connectivity)
        2000-2999 — Perception (state parsing, event detection)
        3000-3999 — Prediction (model inference, feature extraction)
        4000-4999 — Planning (strategy generation)
        5000-5999 — Control (voice output, overlay rendering)
        6000-6999 — Cyber framework (scheduler, node, transport)
        9000-9999 — Unknown / internal
    """

    # ── Success ──────────────────────────────────────────────────────
    OK = 0

    # ── Canbus errors (1000-1999) ────────────────────────────────────
    CANBUS_LCU_CONNECTION_FAILED = 1001
    CANBUS_LCU_TIMEOUT = 1002
    CANBUS_LCU_HTTP_ERROR = 1003
    CANBUS_LCU_INVALID_RESPONSE = 1004
    CANBUS_LCU_SSL_ERROR = 1005
    CANBUS_LCU_NOT_RUNNING = 1006
    CANBUS_FIDDLER_CONNECTION_FAILED = 1101
    CANBUS_FIDDLER_TIMEOUT = 1102
    CANBUS_FIDDLER_DECODE_ERROR = 1103
    CANBUS_FIDDLER_MCP_ERROR = 1104
    CANBUS_GAME_NOT_IN_PROGRESS = 1201
    CANBUS_GAME_ENDED = 1202
    CANBUS_STALE_DATA = 1203

    # ── Perception errors (2000-2999) ────────────────────────────────
    PERCEPTION_STATE_PARSE_ERROR = 2001
    PERCEPTION_STATE_INCOMPLETE = 2002
    PERCEPTION_EVENT_UNKNOWN_TYPE = 2003
    PERCEPTION_PLAYER_NOT_FOUND = 2004
    PERCEPTION_MINIMAP_PARSE_ERROR = 2005
    PERCEPTION_TEAM_RESOLUTION_FAILED = 2006
    PERCEPTION_STALE_STATE = 2007
    PERCEPTION_CHAMPION_ID_UNKNOWN = 2008

    # ── Prediction errors (3000-3999) ─────────────────────────────────
    PREDICTION_MODEL_NOT_LOADED = 3001
    PREDICTION_FEATURE_EXTRACTION_FAILED = 3002
    PREDICTION_INFERENCE_TIMEOUT = 3003
    PREDICTION_INVALID_INPUT = 3004
    PREDICTION_WIN_PROB_OUT_OF_RANGE = 3005
    PREDICTION_TEAMFIGHT_DETECTION_ERROR = 3006
    PREDICTION_OBJECTIVE_TIMING_ERROR = 3007

    # ── Planning errors (4000-4999) ──────────────────────────────────
    PLANNING_STRATEGY_GENERATION_FAILED = 4001
    PLANNING_ITEM_BUILD_ERROR = 4002
    PLANNING_MACRO_DECISION_ERROR = 4003
    PLANNING_NO_VALID_ACTION = 4004
    PLANNING_CONTEXT_INSUFFICIENT = 4005

    # ── Control errors (5000-5999) ───────────────────────────────────
    CONTROL_VOICE_TTS_ERROR = 5001
    CONTROL_VOICE_QUEUE_FULL = 5002
    CONTROL_OVERLAY_RENDER_ERROR = 5003
    CONTROL_ACTION_DISPATCH_FAILED = 5004

    # ── Cyber framework errors (6000-6999) ───────────────────────────
    CYBER_NODE_NOT_INITIALIZED = 6001
    CYBER_CHANNEL_NOT_FOUND = 6002
    CYBER_SCHEDULER_DEPENDENCY_ERROR = 6003
    CYBER_COMPONENT_INIT_FAILED = 6004
    CYBER_TRANSPORT_ERROR = 6005

    # ── Unknown (9000-9999) ──────────────────────────────────────────
    UNKNOWN_ERROR = 9000
    INTERNAL_ERROR = 9001
    NOT_IMPLEMENTED = 9002
    CONFIGURATION_ERROR = 9003


def error_code_module(code: ErrorCode) -> str:
    """Return the module name for a given error code.

    Args:
        code: An ErrorCode value.

    Returns:
        Module name string (e.g., "canbus", "perception").
    """
    val = int(code)
    if val == 0:
        return "ok"
    elif 1000 <= val < 2000:
        return "canbus"
    elif 2000 <= val < 3000:
        return "perception"
    elif 3000 <= val < 4000:
        return "prediction"
    elif 4000 <= val < 5000:
        return "planning"
    elif 5000 <= val < 6000:
        return "control"
    elif 6000 <= val < 7000:
        return "cyber"
    else:
        return "unknown"


@dataclass(frozen=True)
class Status:
    """Immutable status object returned by module operations.

    Supports chaining: an outer operation can wrap an inner Status
    with additional context via ``Status.wrap()``.

    Examples::

        # Success
        return Status.OK()

        # Error with context
        return Status(
            code=ErrorCode.CANBUS_LCU_TIMEOUT,
            message="LCU API did not respond within 2s",
        )

        # Wrapping an inner error
        inner = fetch_game_state()
        if not inner.ok:
            return Status.wrap(
                inner,
                ErrorCode.PERCEPTION_STATE_INCOMPLETE,
                "Cannot assemble state without LCU data",
            )
    """

    code: ErrorCode = ErrorCode.OK
    message: str = ""
    timestamp: float = field(default_factory=time.time)
    details: Dict[str, Any] = field(default_factory=dict)
    inner: Optional["Status"] = None

    # ── Convenience constructors ─────────────────────────────────────

    @staticmethod
    def ok(message: str = "") -> "Status":
        """Create a success status."""
        return Status(code=ErrorCode.OK, message=message)

    @staticmethod
    def error(code: ErrorCode, message: str = "", **details: Any) -> "Status":
        """Create an error status.

        Args:
            code: The error code.
            message: Human-readable description.
            **details: Additional key-value context.
        """
        return Status(code=code, message=message, details=details)

    @staticmethod
    def wrap(
        inner: "Status",
        code: ErrorCode,
        message: str = "",
    ) -> "Status":
        """Wrap an inner status with additional context.

        Args:
            inner: The original error status.
            code: The outer error code.
            message: Additional context message.
        """
        return Status(
            code=code,
            message=message,
            inner=inner,
        )

    # ── Properties ───────────────────────────────────────────────────

    @property
    def ok(self) -> bool:
        """True if this status represents success."""
        return self.code == ErrorCode.OK

    @property
    def module(self) -> str:
        """Module that generated this error."""
        return error_code_module(self.code)

    # ── Chain traversal ──────────────────────────────────────────────

    def chain(self) -> List["Status"]:
        """Return the full error chain from outermost to innermost."""
        result: List[Status] = [self]
        current = self.inner
        while current is not None:
            result.append(current)
            current = current.inner
        return result

    def root_cause(self) -> "Status":
        """Return the innermost (root cause) status."""
        current = self
        while current.inner is not None:
            current = current.inner
        return current

    # ── Serialization ────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        d: Dict[str, Any] = {
            "code": self.code.value,
            "code_name": self.code.name,
            "module": self.module,
            "message": self.message,
            "timestamp": self.timestamp,
            "ok": self.ok,
        }
        if self.details:
            d["details"] = self.details
        if self.inner is not None:
            d["inner"] = self.inner.to_dict()
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Status":
        """Deserialize from a dict."""
        inner = None
        if "inner" in d and d["inner"] is not None:
            inner = Status.from_dict(d["inner"])
        return Status(
            code=ErrorCode(d.get("code", 0)),
            message=d.get("message", ""),
            timestamp=d.get("timestamp", 0.0),
            details=d.get("details", {}),
            inner=inner,
        )

    # ── Display ──────────────────────────────────────────────────────

    def __str__(self) -> str:
        if self.ok:
            return "Status(OK)"
        parts = [f"Status({self.code.name}"]
        if self.message:
            parts.append(f": {self.message}")
        if self.inner is not None:
            parts.append(f" <- {self.inner}")
        parts.append(")")
        return "".join(parts)

    def __bool__(self) -> bool:
        """Allow ``if status:`` to check for success."""
        return self.ok


# ─── Message envelope with status ────────────────────────────────────────────

@dataclass
class StatusMessage:
    """A message envelope that carries both payload and status.

    Used in channel communication when the receiver needs to know
    whether the data is valid or degraded.

    Attributes:
        status: The status of this message's data.
        payload: The actual data payload.
        sequence: Monotonic sequence number.
        source_component: Name of the component that produced this.
        game_time: In-game timestamp (seconds from game start).
    """
    status: Status
    payload: Any = None
    sequence: int = 0
    source_component: str = ""
    game_time: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status.ok

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.to_dict(),
            "sequence": self.sequence,
            "source_component": self.source_component,
            "game_time": self.game_time,
            "has_payload": self.payload is not None,
        }
