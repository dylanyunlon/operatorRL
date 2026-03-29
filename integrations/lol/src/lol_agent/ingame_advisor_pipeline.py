"""
Ingame Advisor Pipeline — Real-time data → strategy fusion → voice narration.

Orchestrates live game advisory: poll live data, fuse with history,
decide strategy, and deliver narrated guidance.

Location: integrations/lol/src/lol_agent/ingame_advisor_pipeline.py

Reference (拿来主义):
  - integrations/lol/src/lol_agent/decision_engine.py: decide pattern
  - integrations/lol/src/lol_agent/voice_narration_engine.py: narration
  - Seraphine/app/lol/connector.py: Live Client Data API polling
"""

from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.ingame_advisor_pipeline.v1"

class IngameAdvisorPipeline:
    """Real-time in-game advisory pipeline."""

    def __init__(self) -> None:
        self._tick_count: int = 0
        self._last_advice: dict[str, Any] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def tick(self, game_state: dict[str, Any], history_ctx: dict[str, Any] = None) -> dict[str, Any]:
        """Process one advisory tick.

        Args:
            game_state: Current live game state (players, objectives, events).
            history_ctx: Optional historical context per opponent.

        Returns:
            Advisory dict with action, reasoning, priority.
        """
        self._tick_count += 1
        history_ctx = history_ctx or {}

        game_time = game_state.get("gameTime", 0.0)
        my_team = game_state.get("myTeam", [])
        enemies = game_state.get("enemies", [])

        # Threat assessment with historical overlay
        threats = []
        for e in enemies:
            puuid = e.get("puuid", "")
            hist = history_ctx.get(puuid, {})
            base_threat = e.get("level", 1) / 18.0
            hist_boost = hist.get("threat_level", 0.0) * 0.3
            threats.append({"champion": e.get("champion", ""), "threat": min(base_threat + hist_boost, 1.0)})

        threats.sort(key=lambda t: t["threat"], reverse=True)
        top_threat = threats[0] if threats else {"champion": "none", "threat": 0.0}

        # Phase-based strategy
        if game_time < 900:
            phase = "laning"
            action = "focus_cs" if top_threat["threat"] < 0.5 else "play_safe"
        elif game_time < 1800:
            phase = "mid_game"
            action = "group_objectives"
        else:
            phase = "late_game"
            action = "team_fight_focus"

        advice = {
            "tick": self._tick_count,
            "phase": phase,
            "action": action,
            "top_threat": top_threat,
            "reasoning": f"{phase}: {action} (top threat: {top_threat['champion']})",
            "timestamp": time.time(),
        }
        self._last_advice = advice
        self._fire_evolution("tick_complete", {"tick": self._tick_count, "phase": phase})
        return advice

    def get_last_advice(self) -> dict[str, Any]:
        return self._last_advice

    def reset(self) -> None:
        self._tick_count = 0
        self._last_advice = {}

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})
