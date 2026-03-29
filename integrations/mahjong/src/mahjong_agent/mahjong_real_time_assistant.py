"""
Mahjong Real-Time Assistant — RealTimeAssistantABC implementation for Mahjong.

Provides real-time tile advice for Mahjong matches:
scouting opponents, discard recommendations, feedback, post-game analysis.

Location: integrations/mahjong/src/mahjong_agent/mahjong_real_time_assistant.py

Reference (拿来主义):
  - Akagi/mjai_bot/controller.py: Controller.react() event-driven decisions
  - integrations/mahjong/src/mahjong_agent/mahjong_strategy_advisor.py: advise pattern
  - integrations/mahjong/src/mahjong_agent/discard_advisor.py: tile evaluation
  - modules/real_time_assistant_abc.py: ABC contract
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import os
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.mahjong.mahjong_real_time_assistant.v1"

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
_ABC_FILE = os.path.join(_ROOT, "modules", "real_time_assistant_abc.py")


def _resolve_abc(filepath: str, cls_name: str) -> type:
    """Find already-loaded ABC class or load from file."""
    abs_path = os.path.abspath(filepath)
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        if f and os.path.abspath(f) == abs_path and hasattr(mod, cls_name):
            return getattr(mod, cls_name)
    spec = importlib.util.spec_from_file_location("modules.real_time_assistant_abc", filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["modules.real_time_assistant_abc"] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, cls_name)


RealTimeAssistantABC = _resolve_abc(_ABC_FILE, "RealTimeAssistantABC")


class MahjongRealTimeAssistant(RealTimeAssistantABC):
    """Mahjong real-time game assistant.

    Implements scout/decide/feedback/postgame for Mahjong,
    mirroring Akagi's controller.react() decision flow
    with shanten-aware tile recommendations.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._feedback_records: list[dict[str, Any]] = []
        self._decision_history: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    @property
    def game_name(self) -> str:
        return "mahjong"

    def scout(self, context: dict[str, Any]) -> dict[str, Any]:
        """Scout opponents and round context.

        Args:
            context: Dict with opponents, wind, round info.

        Returns:
            Scouting report dict.
        """
        opponents = context.get("opponents", [])
        wind = context.get("wind", "unknown")
        round_num = context.get("round", 0)
        return {
            "opponent_count": len(opponents),
            "wind": wind,
            "round": round_num,
            "threat_level": min(1.0, len(opponents) * 0.25),
        }

    def decide(self, state: dict[str, Any]) -> dict[str, Any]:
        """Produce tile recommendation from hand state.

        Uses shanten distance for strategy selection.

        Args:
            state: Dict with hand tiles, discards, shanten.

        Returns:
            Decision dict with action, recommendation, shanten.
        """
        hand = state.get("hand", [])
        shanten = state.get("shanten", 6)

        # Simple strategy: closer to tenpai → more aggressive
        if shanten <= 1:
            action = "riichi_or_damaten"
            confidence = 0.9
        elif shanten <= 3:
            action = "build_hand"
            confidence = 0.6
        else:
            action = "safe_discard"
            confidence = 0.4

        # Recommend discarding last tile if hand is non-empty
        recommendation = hand[-1] if hand else "pass"

        decision = {
            "action": action,
            "recommendation": recommendation,
            "shanten": shanten,
            "confidence": confidence,
            "hand_size": len(hand),
        }
        self._decision_history.append(decision)
        self._fire_evolution({"action": "decide", "shanten": shanten})
        return decision

    def record_feedback(
        self, advice: dict[str, Any], action: dict[str, Any]
    ) -> None:
        """Record advice vs actual action."""
        match = advice.get("action") == action.get("action")
        self._feedback_records.append({
            "advice": advice,
            "action": action,
            "match": match,
            "timestamp": time.time(),
        })

    def post_game_report(self) -> dict[str, Any]:
        """Generate post-game analysis."""
        total = len(self._feedback_records)
        matches = sum(1 for f in self._feedback_records if f.get("match"))
        return {
            "game": "mahjong",
            "total_feedback": total,
            "feedback_count": total,
            "match_count": matches,
            "match_rate": matches / max(total, 1),
            "decisions_made": len(self._decision_history),
        }

    # --- Evolution pattern ---
    def _fire_evolution(self, detail: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "key": _EVOLUTION_KEY,
                    "detail": detail,
                    "timestamp": time.time(),
                })
            except Exception:
                logger.warning("Evolution callback error (mahjong_real_time_assistant)")
