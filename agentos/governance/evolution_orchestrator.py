"""
Evolution Orchestrator — Cross-game self-evolution scheduling.

Manages multiple game evolution loops, running cycles, allocating
resources proportionally, and collecting fitness reports.

Location: agentos/governance/evolution_orchestrator.py

Reference (拿来主义):
  - agentlightning/trainer/multi_game_trainer.py: train_step per game pattern
  - agentlightning/algorithm/self_play_scheduler.py: scheduling across agents
  - modules/evolution_loop_abc.py: EvolutionLoopABC contract
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional, Protocol

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.evolution_orchestrator.v1"


class EvolutionLoopProtocol(Protocol):
    """Protocol matching EvolutionLoopABC interface."""
    def compute_fitness(self) -> float: ...
    def should_evolve(self) -> bool: ...
    def advance_generation(self) -> None: ...
    def export_training_data(self) -> list: ...
    def reset(self) -> None: ...


class EvolutionOrchestrator:
    """Cross-game evolution orchestrator.

    Manages registered evolution loops, runs cycles, allocates
    resources based on fitness, and fires evolution callbacks.

    Attributes:
        total_budget: Total resource budget for allocation.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self, total_budget: int = 100) -> None:
        self.total_budget = total_budget
        self._loops: dict[str, Any] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_loop(self, game: str, loop: Any) -> None:
        """Register an evolution loop for a game.

        Args:
            game: Game identifier.
            loop: Object implementing EvolutionLoopABC interface.
        """
        self._loops[game] = loop

    def unregister_loop(self, game: str) -> None:
        """Remove an evolution loop."""
        self._loops.pop(game, None)

    def loop_count(self) -> int:
        """Number of registered loops."""
        return len(self._loops)

    def run_cycle(self) -> dict[str, Any]:
        """Run one evolution cycle across all registered loops.

        For each loop that should_evolve(), advances generation
        and exports training data.

        Returns:
            Dict with per-game cycle results.
        """
        results = {}
        for game, loop in self._loops.items():
            if loop.should_evolve():
                loop.advance_generation()
                spans = loop.export_training_data()
                results[game] = {
                    "evolved": True,
                    "spans_exported": len(spans),
                    "fitness": loop.compute_fitness(),
                }
            else:
                results[game] = {
                    "evolved": False,
                    "fitness": loop.compute_fitness(),
                }

        self._fire_evolution("cycle_completed", {
            "games_evolved": sum(1 for r in results.values() if r.get("evolved")),
            "total_games": len(results),
        })
        return results

    def allocate_resources(self) -> dict[str, int]:
        """Allocate resources proportionally to fitness.

        Higher fitness → more resources. Minimum 1 per game.

        Returns:
            Dict of game → allocated resource units.
        """
        if not self._loops:
            return {}

        fitnesses = {}
        for game, loop in self._loops.items():
            fitnesses[game] = max(loop.compute_fitness(), 0.01)

        total_fitness = sum(fitnesses.values())
        allocation = {}
        remaining = self.total_budget

        for game, fitness in fitnesses.items():
            share = max(1, int(self.total_budget * fitness / total_fitness))
            allocation[game] = share
            remaining -= share

        # Distribute remaining to highest fitness
        if remaining > 0 and fitnesses:
            best = max(fitnesses, key=fitnesses.get)
            allocation[best] += remaining

        return allocation

    def get_fitness_report(self) -> dict[str, float]:
        """Get fitness for all registered games.

        Returns:
            Dict of game → fitness score.
        """
        return {game: loop.compute_fitness() for game, loop in self._loops.items()}

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })
