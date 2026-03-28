"""
Fiddler-Vision Comparator — Cross-validates protocol and vision data.

Compares Fiddler-captured network data with vision-extracted data to
measure consistency, detect hallucinations in OCR, and validate the
protocol capture pipeline.

Location: extensions/vision-bridge/src/vision_bridge/fiddler_vision_comparator.py

Reference (拿来主義):
  - operatorRL fiddler-bridge fiddler_evolution_bridge.py: data comparison
  - operatorRL vision_protocol_fusion: field-level matching
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.vision_bridge.fiddler_vision_comparator.v1"


class FiddlerVisionComparator:
    """Compares Fiddler protocol data with vision-extracted data.

    Tracks comparison history and generates accuracy reports.

    Attributes:
        tolerance: Relative tolerance for numeric comparison.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self, tolerance: float = 0.05) -> None:
        self.tolerance = tolerance
        self._history: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable] = None

    def compare(
        self,
        fiddler_data: dict[str, Any],
        vision_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Compare Fiddler and vision data field-by-field.

        Args:
            fiddler_data: Data from network capture.
            vision_data: Data from screen capture/OCR.

        Returns:
            Dict with 'match' (bool), 'accuracy' (float),
            'field_results' (per-field comparison).
        """
        if not fiddler_data and not vision_data:
            result = {"match": True, "accuracy": 1.0, "field_results": {}}
            self._history.append(result)
            return result

        all_keys = set(fiddler_data.keys()) | set(vision_data.keys())
        # Remove metadata keys
        all_keys -= {"timestamp", "source", "confidence", "conflict"}

        if not all_keys:
            result = {"match": True, "accuracy": 1.0, "field_results": {}}
            self._history.append(result)
            return result

        field_results: dict[str, dict[str, Any]] = {}
        matches = 0
        total = 0

        for key in all_keys:
            f_val = fiddler_data.get(key)
            v_val = vision_data.get(key)

            if f_val is None or v_val is None:
                field_results[key] = {
                    "match": f_val is None and v_val is None,
                    "fiddler": f_val,
                    "vision": v_val,
                }
                total += 1
                if f_val is None and v_val is None:
                    matches += 1
                continue

            field_match = self._values_match(f_val, v_val)
            field_results[key] = {
                "match": field_match,
                "fiddler": f_val,
                "vision": v_val,
            }
            total += 1
            if field_match:
                matches += 1

        accuracy = matches / total if total > 0 else 1.0
        overall_match = accuracy == 1.0

        result = {
            "match": overall_match,
            "accuracy": accuracy,
            "field_results": field_results,
        }
        self._history.append(result)
        return result

    def generate_report(self) -> dict[str, Any]:
        """Generate an accuracy report from comparison history.

        Returns:
            Dict with 'total_comparisons', 'match_rate', 'avg_accuracy'.
        """
        n = len(self._history)
        if n == 0:
            return {
                "total_comparisons": 0,
                "match_rate": 0.0,
                "avg_accuracy": 0.0,
            }

        match_count = sum(1 for r in self._history if r["match"])
        avg_accuracy = sum(r["accuracy"] for r in self._history) / n

        return {
            "total_comparisons": n,
            "match_rate": match_count / n,
            "avg_accuracy": avg_accuracy,
        }

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
