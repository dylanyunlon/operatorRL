"""
Vision-Protocol Fusion — Merges Fiddler network data with vision-extracted data.

Combines protocol-captured data (exact, low-latency) with vision-captured data
(visual verification) to produce high-confidence game state, with conflict
detection and timestamp alignment.

Location: extensions/vision-bridge/src/vision_bridge/vision_protocol_fusion.py

Reference (拿来主義):
  - operatorRL fiddler-bridge realtime_stream: protocol event handling
  - operatorRL fiddler-bridge combat_calculator: game state fields
  - operatorRL fiddler_vision_comparator: consistency checking pattern
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.vision_bridge.vision_protocol_fusion.v1"

# Default tolerance for numeric field comparison
_DEFAULT_TOLERANCE = 0.05  # 5%


class VisionProtocolFusion:
    """Fuses protocol-captured and vision-captured game data.

    Priority modes:
    - 'protocol': Trust protocol data when available (default).
    - 'vision': Trust vision data when available.
    - 'consensus': Require both sources to agree.

    Attributes:
        priority: Which source to trust when both are available.
        tolerance: Numeric tolerance for match detection.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(
        self,
        priority: str = "protocol",
        tolerance: float = _DEFAULT_TOLERANCE,
    ) -> None:
        self.priority = priority
        self.tolerance = tolerance
        self.evolution_callback: Optional[Callable] = None

    def fuse(
        self,
        protocol_data: Optional[dict[str, Any]],
        vision_data: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        """Fuse protocol and vision data into a unified game state.

        Args:
            protocol_data: Data from Fiddler/network capture (or None).
            vision_data: Data from screen capture/OCR (or None).

        Returns:
            Fused dict with 'source', 'confidence', and game state fields.
            If conflict detected, includes 'conflict': True.
        """
        if protocol_data is None and vision_data is None:
            return {}

        if protocol_data is not None and vision_data is None:
            result = dict(protocol_data)
            result["source"] = "protocol"
            result["confidence"] = 0.95
            return result

        if protocol_data is None and vision_data is not None:
            result = dict(vision_data)
            result["source"] = "vision"
            result["confidence"] = 0.7
            return result

        # Both sources available — fuse
        assert protocol_data is not None and vision_data is not None
        result: dict[str, Any] = {}
        has_conflict = False

        # Merge all keys from both sources
        all_keys = set(protocol_data.keys()) | set(vision_data.keys())
        # Remove metadata keys from comparison
        all_keys -= {"timestamp", "source", "confidence", "conflict"}

        for key in all_keys:
            p_val = protocol_data.get(key)
            v_val = vision_data.get(key)

            if p_val is not None and v_val is not None:
                # Both have this field — check agreement
                if self._values_match(p_val, v_val):
                    # Agreement — use priority source
                    result[key] = p_val if self.priority == "protocol" else v_val
                else:
                    # Conflict
                    has_conflict = True
                    result[key] = p_val if self.priority == "protocol" else v_val
            elif p_val is not None:
                result[key] = p_val
            else:
                result[key] = v_val

        result["source"] = "fused"
        result["confidence"] = 0.6 if has_conflict else 0.95
        if has_conflict:
            result["conflict"] = True

        return result

    def align_timestamps(
        self,
        protocol_ts: float,
        vision_ts: float,
        max_drift: float = 1.0,
    ) -> bool:
        """Check if two timestamps are within acceptable drift.

        Args:
            protocol_ts: Protocol data timestamp.
            vision_ts: Vision data timestamp.
            max_drift: Maximum allowed time difference in seconds.

        Returns:
            True if timestamps are aligned (within max_drift).
        """
        return abs(protocol_ts - vision_ts) <= max_drift

    def _values_match(self, a: Any, b: Any) -> bool:
        """Check if two values match within tolerance."""
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if a == 0 and b == 0:
                return True
            denom = max(abs(a), abs(b), 1)
            return abs(a - b) / denom <= self.tolerance
        return a == b

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)
