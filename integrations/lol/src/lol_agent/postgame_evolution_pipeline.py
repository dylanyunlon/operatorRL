"""
Postgame Evolution Pipeline — Feedback → fitness → generational advance.

Collects postgame stats, computes fitness score, and fires
evolution events for the training pipeline.

Location: integrations/lol/src/lol_agent/postgame_evolution_pipeline.py

Reference (拿来主义):
  - integrations/lol/src/lol_agent/post_game_analyzer.py: stats collection
  - integrations/lol/src/lol_agent/lol_evolution_loop.py: evolution events
"""

from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.postgame_evolution_pipeline.v1"

class PostgameEvolutionPipeline:
    """Postgame analysis → evolution training pipeline."""

    def __init__(self) -> None:
        self._generation: int = 0
        self._fitness_history: list[float] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def process(self, game_result: dict[str, Any], advices_given: list[dict[str, Any]] = None) -> dict[str, Any]:
        """Process a completed game for evolution.

        Args:
            game_result: Final game stats (win, kills, deaths, assists, cs, gold, duration).
            advices_given: List of advices given during game (for feedback accuracy).

        Returns:
            Evolution event with fitness score and generation info.
        """
        advices_given = advices_given or []
        won = game_result.get("win", False)
        kills = game_result.get("kills", 0)
        deaths = max(game_result.get("deaths", 0), 1)
        assists = game_result.get("assists", 0)
        kda = (kills + assists) / deaths
        cs = game_result.get("cs", 0)
        duration_min = max(game_result.get("duration", 1) / 60.0, 1.0)
        cs_per_min = cs / duration_min

        # Fitness: weighted composite
        win_score = 1.0 if won else 0.0
        kda_score = min(kda / 10.0, 1.0)
        cs_score = min(cs_per_min / 10.0, 1.0)
        fitness = 0.50 * win_score + 0.30 * kda_score + 0.20 * cs_score

        # Advice accuracy (how many advices were followed with positive outcome)
        advice_accuracy = 0.0
        if advices_given:
            followed = sum(1 for a in advices_given if a.get("followed", False))
            advice_accuracy = followed / len(advices_given)

        self._fitness_history.append(fitness)
        self._generation += 1

        event = {
            "generation": self._generation,
            "fitness": fitness,
            "won": won,
            "kda": kda,
            "cs_per_min": cs_per_min,
            "advice_accuracy": advice_accuracy,
            "timestamp": time.time(),
        }
        self._fire_evolution("postgame_processed", event)
        return event

    def get_fitness_trend(self, window: int = 10) -> dict[str, Any]:
        recent = self._fitness_history[-window:] if self._fitness_history else []
        if not recent:
            return {"avg": 0.0, "trend": "neutral", "count": 0}
        avg = sum(recent) / len(recent)
        if len(recent) >= 2:
            first_half = sum(recent[:len(recent)//2]) / max(len(recent)//2, 1)
            second_half = sum(recent[len(recent)//2:]) / max(len(recent) - len(recent)//2, 1)
            trend = "improving" if second_half > first_half * 1.05 else ("declining" if second_half < first_half * 0.95 else "stable")
        else:
            trend = "neutral"
        return {"avg": avg, "trend": trend, "count": len(recent)}

    def get_generation(self) -> int:
        return self._generation

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})
