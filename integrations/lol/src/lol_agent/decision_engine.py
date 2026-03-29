"""
Decision Engine — Aggregates game state, history, threats into strategy.

Combines gold/kill/tower/dragon advantages with game phase to produce
actionable strategy decisions. Mirrors dota2bot mode selection.

Location: integrations/lol/src/lol_agent/decision_engine.py

Reference (拿来主义):
  - dota2bot-OpenHyperAI: mode_push/mode_farm/mode_retreat decision logic
  - integrations/lol/src/lol_agent/lol_strategy_advisor.py: phase detection + advantage scoring
  - DI-star: rl_learner.py decision output format
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.decision_engine.v1"

_EARLY_END = 840.0   # 14 min
_MID_END = 1800.0    # 30 min


class DecisionEngine:
    """Aggregated decision engine for LoL gameplay.

    Combines game state features into strategic decisions,
    tracking decision history for learning feedback.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._history: list[dict[str, Any]] = []
        self._decision_count: int = 0

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def decide(self, state: dict[str, Any]) -> dict[str, Any]:
        """Produce a strategic decision from aggregated state.

        Args:
            state: Dict with game_time, gold_diff, kill_diff,
                   threats, dragon_available, teamfight_rating, etc.

        Returns:
            Dict with action, phase, confidence, suggestion.
        """
        game_time = state.get("game_time", 0.0)
        gold_diff = state.get("gold_diff", 0)
        kill_diff = state.get("kill_diff", 0)
        tower_diff = state.get("tower_diff", 0)
        threats = state.get("threats", [])
        dragon_available = state.get("dragon_available", False)

        # Phase detection (mirrors lol_strategy_advisor.py)
        if game_time < _EARLY_END:
            phase = "early"
        elif game_time < _MID_END:
            phase = "mid"
        else:
            phase = "late"

        # Advantage score (mirrors dota2bot mode priority calculation)
        advantage = gold_diff / 1000.0 + kill_diff * 0.5 + tower_diff * 1.5

        # Threat adjustment
        max_threat = max((t.get("threat_score", 0) for t in threats), default=0)
        if max_threat > 7.0:
            advantage -= 1.5

        # Decision logic (mirrors dota2bot mode_*.lua)
        if phase == "early":
            if advantage > 2:
                action, suggestion = "aggressive_lane", "Press advantage, zone enemy"
            elif advantage < -2:
                action, suggestion = "safe_farm", "Farm safely, avoid trades"
            else:
                action, suggestion = "balanced_farm", "Focus CS, trade when favorable"
        elif phase == "mid":
            if dragon_available and advantage > 0:
                action, suggestion = "contest_dragon", "Group for dragon fight"
            elif advantage > 3:
                action, suggestion = "push_objectives", "Take towers, invade jungle"
            elif advantage < -3:
                action, suggestion = "defensive_vision", "Ward defensively, catch waves"
            else:
                action, suggestion = "skirmish", "Look for picks, contest vision"
        else:
            if advantage > 4:
                action, suggestion = "force_baron", "Force baron, end the game"
            elif advantage < -4:
                action, suggestion = "turtle", "Defend base, look for picks"
            else:
                action, suggestion = "team_fight", "Group for elder/baron fight"

        confidence = min(1.0, max(0.1, 0.3 + abs(advantage) * 0.1))

        decision = {
            "action": action,
            "suggestion": suggestion,
            "phase": phase,
            "advantage": advantage,
            "confidence": confidence,
            "timestamp": time.time(),
        }

        self._history.append(decision)
        self._decision_count += 1

        self._fire_evolution("decision_made", {
            "action": action,
            "phase": phase,
            "advantage": advantage,
        })

        return decision

    def get_history(self) -> list[dict[str, Any]]:
        """Return decision history."""
        return list(self._history)

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })
