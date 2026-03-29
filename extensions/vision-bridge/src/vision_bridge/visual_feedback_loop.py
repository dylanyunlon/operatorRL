"""
Visual Feedback Loop — Detect screen changes and generate operation feedback.

Compares consecutive game state frames, detects significant changes,
classifies severity, and maintains feedback history.

Location: extensions/vision-bridge/src/vision_bridge/visual_feedback_loop.py

Reference (拿来主義):
  - extensions/vision-bridge/src/vision_bridge/vision_evolution_bridge.py: feedback loop
  - extensions/vision-bridge/src/vision_bridge/fiddler_vision_comparator.py: comparison
  - operatorRL: evolution callback pattern
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.vision_bridge.visual_feedback_loop.v1"

# Fields to monitor and their normalization denominators
_MONITORED_FIELDS: dict[str, float] = {
    "gold": 20000.0,
    "health": 3000.0,
    "game_time": 3600.0,
}


class VisualFeedbackLoop:
    """Detects significant game state changes and generates feedback.

    Attributes:
        change_threshold: Minimum normalized change to trigger feedback.
        evolution_callback: Optional evolution event callback.
    """

    def __init__(self, change_threshold: float = 0.1) -> None:
        self.change_threshold = change_threshold
        self._prev_state: Optional[dict[str, Any]] = None
        self._history: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def set_state(self, state: dict[str, Any]) -> None:
        """Set the current baseline state."""
        self._prev_state = dict(state)

    def detect_change(self, frame_a: dict[str, Any], frame_b: dict[str, Any]) -> bool:
        """Detect if there is a significant change between two frames."""
        max_delta = 0.0
        for field, denom in _MONITORED_FIELDS.items():
            va = frame_a.get(field, 0)
            vb = frame_b.get(field, 0)
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                delta = abs(vb - va) / max(denom, 1.0)
                max_delta = max(max_delta, delta)
        return max_delta >= self.change_threshold

    def generate_feedback(
        self, prev: dict[str, Any], curr: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate feedback detailing what changed and severity.

        Args:
            prev: Previous state dict.
            curr: Current state dict.

        Returns:
            Dict with 'changes' list, each with field, delta, severity.
        """
        changes: list[dict[str, Any]] = []
        for field, denom in _MONITORED_FIELDS.items():
            va = prev.get(field, 0)
            vb = curr.get(field, 0)
            if not isinstance(va, (int, float)) or not isinstance(vb, (int, float)):
                continue
            delta = vb - va
            norm_delta = abs(delta) / max(denom, 1.0)
            if norm_delta < 0.01:
                continue
            severity = "low"
            if norm_delta > 0.3:
                severity = "critical"
            elif norm_delta > 0.1:
                severity = "high"
            elif norm_delta > 0.05:
                severity = "medium"
            changes.append({
                "field": field,
                "delta": delta,
                "normalized_delta": norm_delta,
                "severity": severity,
            })
        return {"changes": changes, "timestamp": time.time()}

    def process_tick(self, current_state: dict[str, Any]) -> dict[str, Any]:
        """Process one tick: compare with previous state and generate feedback.

        Args:
            current_state: Current game state dict.

        Returns:
            Dict with 'changed' bool and 'feedback' dict.
        """
        if self._prev_state is None:
            self._prev_state = dict(current_state)
            return {"changed": False, "feedback": {"changes": []}}

        changed = self.detect_change(self._prev_state, current_state)
        feedback = self.generate_feedback(self._prev_state, current_state)

        self._history.append({
            "changed": changed,
            "feedback": feedback,
            "state": dict(current_state),
            "timestamp": time.time(),
        })

        self._fire_evolution({
            "event": "tick_processed",
            "changed": changed,
            "changes": len(feedback["changes"]),
        })

        self._prev_state = dict(current_state)
        return {"changed": changed, "feedback": feedback}

    def get_history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def clear_history(self) -> None:
        self._history.clear()

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
