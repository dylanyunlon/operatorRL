"""
Dota2 Voice Advisor — Dota2-specific voice guidance system.

Generates situational advice (farming, teamfight, Roshan) with
priority queue for voice output.

Location: integrations/dota2/src/dota2_agent/dota2_voice_advisor.py

Reference (拿来主义):
  - integrations/lol/src/lol_agent/voice_narration_engine.py: priority queue
  - dota2bot-OpenHyperAI: Dota2 game state patterns
  - integrations/dota2/src/dota2_agent/bot_commander.py: command patterns
  - operatorRL: evolution callback pattern
"""

from __future__ import annotations

import heapq
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.dota2.dota2_voice_advisor.v1"


class Dota2VoiceAdvisor:
    """Dota2-specific voice advisory system.

    Attributes:
        language: Output language code.
        evolution_callback: Optional evolution event callback.
    """

    def __init__(self, language: str = "en") -> None:
        self.language = language
        self._heap: list[tuple[int, int, str]] = []
        self._seq: int = 0
        self._history: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def generate_advice(self, state: dict[str, Any]) -> str:
        """Generate general situational advice.

        Args:
            state: Dict with game_time, hero, gold, phase.

        Returns:
            Advice string.
        """
        hero = state.get("hero", "Unknown")
        phase = state.get("phase", "mid")
        gold = state.get("gold", 0)
        game_time = state.get("game_time", 0)

        if phase == "laning" or game_time < 600:
            advice = f"Focus on last hits with {hero}. Current gold: {gold}."
        elif phase == "mid" or game_time < 1500:
            advice = f"Look for ganks or objectives with {hero}. Gold: {gold}."
        else:
            advice = f"Late game — stay with your team, {hero}. Gold: {gold}."

        self._history.append({"advice": advice, "timestamp": time.time(), "state": state})
        self._fire_evolution({"event": "advice_generated", "hero": hero, "phase": phase})
        return advice

    def get_roshan_advice(self, state: dict[str, Any]) -> str:
        status = state.get("roshan_status", "alive")
        if status == "dead":
            death_time = state.get("roshan_death_time", 0)
            game_time = state.get("game_time", 0)
            elapsed = game_time - death_time
            if elapsed > 480:  # 8 min respawn
                return "Roshan may have respawned. Check the pit."
            return f"Roshan respawns in ~{max(480 - int(elapsed), 0)} seconds."
        return "Roshan is alive. Coordinate with your team before engaging."

    def get_teamfight_advice(self, state: dict[str, Any]) -> str:
        nearby_enemies = state.get("nearby_enemies", 0)
        nearby_allies = state.get("nearby_allies", 0)
        ult_ready = state.get("ult_ready", False)
        hero = state.get("hero", "Unknown")
        hp_pct = state.get("hero_hp_pct", 1.0)

        if hp_pct < 0.3:
            return "Low HP — fall back and heal before fighting."
        if nearby_allies > nearby_enemies and ult_ready:
            return f"Favorable fight as {hero}. Use ultimate if you can engage safely."
        if nearby_enemies > nearby_allies:
            return "Outnumbered — back off and wait for teammates."
        return "Even fight — play carefully around cooldowns."

    def get_farming_advice(self, state: dict[str, Any]) -> str:
        cs_per_min = state.get("cs_per_min", 0.0)
        gold = state.get("gold", 0)
        target_cost = state.get("target_item_cost", 0)

        if cs_per_min < 5.0:
            return "Farm rate is low. Focus on last-hitting creeps."
        if target_cost > 0 and gold < target_cost:
            remaining = target_cost - gold
            return f"Need {remaining} more gold for your item. Keep farming efficiently."
        return "Good farm pace. Consider pushing or taking objectives."

    def enqueue(self, message: str, priority: int = 5) -> None:
        self._seq += 1
        heapq.heappush(self._heap, (priority, self._seq, message))

    def dequeue(self) -> Optional[dict[str, Any]]:
        if not self._heap:
            return None
        priority, _seq, message = heapq.heappop(self._heap)
        return {"text": message, "priority": priority}

    def set_language(self, language: str) -> None:
        self.language = language

    def get_history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
