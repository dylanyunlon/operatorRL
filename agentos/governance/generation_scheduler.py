"""
Generation Scheduler — Schedule generation advances based on multi-game fitness.
Location: agentos/governance/generation_scheduler.py
Reference: DI-star training loop, ELF self-play scheduler
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "agentos.governance.generation_scheduler.v1"

class GenerationScheduler:
    def __init__(self, min_episodes: int = 10, min_fitness: float = 0.5,
                 cooldown_seconds: float = 0.0) -> None:
        self._min_episodes = min_episodes
        self._min_fitness = min_fitness
        self._cooldown = cooldown_seconds
        self.current_generation: int = 0
        self._episodes: list[dict[str, Any]] = []
        self._fitness: float = 0.0
        self._last_advance: float = 0.0
        self._gen_history: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def record_episode(self, game: str, episode: dict[str, Any]) -> None:
        self._episodes.append({"game": game, **episode, "timestamp": time.time()})
        if self.evolution_callback:
            self.evolution_callback({"type": "episode_recorded", "key": _EVOLUTION_KEY,
                                     "game": game, "timestamp": time.time()})

    def set_fitness(self, fitness: float) -> None:
        self._fitness = min(max(fitness, 0.0), 1.0)

    def episode_count(self) -> int:
        return len(self._episodes)

    def should_advance(self) -> bool:
        if len(self._episodes) < self._min_episodes:
            return False
        if self._fitness < self._min_fitness:
            return False
        if self._cooldown > 0 and self._last_advance > 0:
            if time.time() - self._last_advance < self._cooldown:
                return False
        return True

    def advance(self) -> None:
        self._gen_history.append({"generation": self.current_generation,
                                  "episodes": len(self._episodes), "fitness": self._fitness,
                                  "advanced_at": time.time()})
        self.current_generation += 1
        self._episodes.clear()
        self._last_advance = time.time()

    def generation_history(self) -> list[dict[str, Any]]:
        return list(self._gen_history)

    def status(self) -> dict[str, Any]:
        return {"current_generation": self.current_generation,
                "episode_count": len(self._episodes), "fitness": self._fitness}
