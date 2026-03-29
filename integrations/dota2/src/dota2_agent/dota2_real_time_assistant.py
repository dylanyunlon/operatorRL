"""
Dota2 Real-Time Assistant — RealTimeAssistantABC implementation for Dota 2.

Provides real-time game advice for Dota 2 matches:
scouting, decision-making, feedback recording, and post-game analysis.

Location: integrations/dota2/src/dota2_agent/dota2_real_time_assistant.py

Reference (拿来主义):
  - dota2bot-OpenHyperAI/mode_push.lua: push/farm/retreat decisions
  - integrations/dota2/src/dota2_agent/bot_commander.py: command priority
  - integrations/lol/src/lol_agent/decision_engine.py: phase-based strategy
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

_EVOLUTION_KEY: str = "integrations.dota2.dota2_real_time_assistant.v1"

# Resolve ABC: reuse already-loaded module (handles test _load() scenario)
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

# Game phase thresholds (seconds) — mirrors dota2bot mode switching
_EARLY_END: float = 720.0   # 12 min laning
_MID_END: float = 1800.0    # 30 min midgame


class Dota2RealTimeAssistant(RealTimeAssistantABC):
    """Dota 2 real-time game assistant.

    Implements scout/decide/feedback/postgame for Dota 2,
    using phase-based strategy (laning → midgame → lategame)
    mirroring dota2bot-OpenHyperAI's mode system.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._feedback_records: list[dict[str, Any]] = []
        self._decision_history: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    @property
    def game_name(self) -> str:
        return "dota2"

    def scout(self, context: dict[str, Any]) -> dict[str, Any]:
        """Scout allies and enemies pre-game or during.

        Args:
            context: Dict with allies, enemies lists.

        Returns:
            Scouting report dict.
        """
        allies = context.get("allies", [])
        enemies = context.get("enemies", [])
        return {
            "ally_count": len(allies),
            "enemy_count": len(enemies),
            "threat_level": min(1.0, len(enemies) * 0.2),
        }

    def decide(self, state: dict[str, Any]) -> dict[str, Any]:
        """Produce Dota 2 strategy decision.

        Uses game_time for phase detection and gold_diff for aggression.

        Args:
            state: Dict with game_time, gold_diff.

        Returns:
            Strategy dict with phase, action, confidence.
        """
        game_time = state.get("game_time", 0.0)
        gold_diff = state.get("gold_diff", 0)

        # Phase detection (dota2bot mode_push/farm/retreat)
        if game_time < _EARLY_END:
            phase = "early"
            if gold_diff > 500:
                action = "aggressive_lane"
            else:
                action = "safe_farm"
        elif game_time < _MID_END:
            phase = "mid"
            if gold_diff > 2000:
                action = "push_tower"
            elif gold_diff < -2000:
                action = "defensive_farm"
            else:
                action = "team_fight"
        else:
            phase = "late"
            if gold_diff > 5000:
                action = "push_high_ground"
            else:
                action = "roshan_then_push"

        confidence = min(1.0, 0.5 + abs(gold_diff) / 10000.0)

        decision = {
            "phase": phase,
            "action": action,
            "strategy": action,
            "confidence": confidence,
            "game_time": game_time,
        }
        self._decision_history.append(decision)
        self._fire_evolution({"action": "decide", "phase": phase})
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
            "game": "dota2",
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
                logger.warning("Evolution callback error (dota2_real_time_assistant)")
