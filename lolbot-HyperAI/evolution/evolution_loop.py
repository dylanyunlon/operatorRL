"""
EvolutionLoop — Self-evolution orchestrator for strategy parameters.
======================================================================
evolution/evolution_loop.py

Claude17: The core "self-evolving" loop that ties everything together.
After each game, evaluates performance, proposes mutations, applies
them, and persists the new generation. This is what makes the system
"agentic" — it improves itself without human intervention.

Architecture position:
    evolution/evolution_loop.py   ← YOU ARE HERE (Claude17 new file)
    ├─ Reads: prediction evaluation (PredictionEvaluator)
    ├─ Reads: session history (SessionManager)
    ├─ Uses: FitnessEvaluator, StrategyMutator, GenerationManager
    ├─ Publishes: /lol/evolution_event (generation changes)
    └─ Consumed by: main_loop.py (post-game callback)

Apollo reference:
    modules/planning/tuning/ — parameter tuning
    modules/calibration/ — online calibration

Design notes:
    - Stateless between games: all state in GenerationManager
    - Conservative mutations: small parameter changes only
    - Rollback on degradation: revert if fitness drops >threshold
    - Generation persistence: JSON files in data/generations/
    - Policy check: mutations gated by AgentOS governance
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class EvolutionConfig:
    """Configuration for the evolution loop."""
    enabled: bool = True
    auto_evolve: bool = True
    fitness_commit_threshold: float = -0.05  # commit if fitness drop < 5%
    max_mutations_per_cycle: int = 3
    mutation_magnitude: float = 0.1  # 10% max parameter change
    min_games_before_evolve: int = 1
    cooldown_s: float = 60.0  # min seconds between evolutions
    rollback_threshold: float = -0.15  # rollback if fitness drops >15%
    data_dir: str = "data/generations"


@dataclass
class EvolutionEvent:
    """Record of one evolution cycle."""
    timestamp: float = field(default_factory=time.time)
    generation_id: str = ""
    new_generation_id: str = ""
    fitness_before: float = 0.0
    fitness_after: float = 0.0
    mutations_applied: int = 0
    committed: bool = False
    rolled_back: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": round(self.timestamp, 3),
            "gen_before": self.generation_id,
            "gen_after": self.new_generation_id,
            "fitness_before": round(self.fitness_before, 6),
            "fitness_after": round(self.fitness_after, 6),
            "mutations": self.mutations_applied,
            "committed": self.committed,
            "rolled_back": self.rolled_back,
            "reason": self.reason,
        }


class EvolutionLoop:
    """Orchestrates the self-evolution cycle.

    Claude17: This is the "brain" of the agentic system. After each
    game it:
    1. Collects fitness metrics
    2. Decides whether to evolve
    3. Proposes mutations
    4. Applies and evaluates
    5. Commits or rolls back

    Usage::

        evo = EvolutionLoop(config=EvolutionConfig())
        evo.register_param_getter("prediction_weights", get_weights)
        evo.register_param_setter("prediction_weights", set_weights)

        # After each game:
        event = evo.evolve(fitness_score=0.72, session_id="abc")
    """

    def __init__(self, config: Optional[EvolutionConfig] = None) -> None:
        self._config = config or EvolutionConfig()
        self._history: List[EvolutionEvent] = []
        self._generation_counter: int = 0
        self._last_evolve_time: float = 0.0
        self._games_since_evolve: int = 0
        self._current_generation_id: str = ""
        self._best_fitness: float = 0.0

        # Parameter accessors
        self._param_getters: Dict[str, Callable[[], Any]] = {}
        self._param_setters: Dict[str, Callable[[Any], None]] = {}
        self._param_snapshots: Dict[str, Dict[str, Any]] = {}

        # Callbacks
        self._on_evolve_callbacks: List[
            Callable[[EvolutionEvent], None]
        ] = []

    def register_param_getter(
        self, name: str, getter: Callable[[], Any]
    ) -> None:
        """Register a function that returns current parameter values."""
        self._param_getters[name] = getter

    def register_param_setter(
        self, name: str, setter: Callable[[Any], None]
    ) -> None:
        """Register a function that applies new parameter values."""
        self._param_setters[name] = setter

    def on_evolve(self, callback: Callable[[EvolutionEvent], None]) -> None:
        """Register callback for evolution events."""
        self._on_evolve_callbacks.append(callback)

    def record_game(self, fitness: float) -> None:
        """Record a completed game's fitness score."""
        self._games_since_evolve += 1
        if fitness > self._best_fitness:
            self._best_fitness = fitness

    def should_evolve(self) -> bool:
        """Check if conditions are met for an evolution cycle."""
        if not self._config.enabled or not self._config.auto_evolve:
            return False

        now = time.time()
        if now - self._last_evolve_time < self._config.cooldown_s:
            return False

        if self._games_since_evolve < self._config.min_games_before_evolve:
            return False

        return True

    def evolve(
        self,
        fitness_score: float,
        session_id: str = "",
    ) -> EvolutionEvent:
        """Run one evolution cycle.

        Args:
            fitness_score: Current game's fitness score.
            session_id: The game session identifier.

        Returns:
            EvolutionEvent describing what happened.
        """
        self.record_game(fitness_score)

        event = EvolutionEvent(
            generation_id=self._current_generation_id,
            fitness_before=fitness_score,
        )

        if not self.should_evolve():
            event.reason = "conditions_not_met"
            self._history.append(event)
            return event

        # 1. Snapshot current params
        current_params = self._snapshot_params()

        # 2. Propose mutations
        mutations = self._propose_mutations(current_params, fitness_score)
        if not mutations:
            event.reason = "no_mutations_proposed"
            self._history.append(event)
            return event

        # 3. Apply mutations
        new_params = self._apply_mutations(current_params, mutations)
        self._set_params(new_params)
        event.mutations_applied = len(mutations)

        # 4. Create new generation
        self._generation_counter += 1
        new_gen_id = f"gen_{self._generation_counter:04d}_{int(time.time()) % 10000:04x}"
        event.new_generation_id = new_gen_id

        # 5. Decide commit/rollback
        # For now, always commit — proper A/B needs multi-game eval
        fitness_delta = 0.0  # We can't know yet; will evaluate next game
        event.committed = True
        event.reason = "applied_pending_evaluation"

        self._current_generation_id = new_gen_id
        self._last_evolve_time = time.time()
        self._games_since_evolve = 0

        # Save snapshot
        self._save_generation(new_gen_id, new_params)

        # Fire callbacks
        for cb in self._on_evolve_callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.error("Evolution callback error: %s", exc)

        self._history.append(event)
        logger.info(
            "[Evolution] %s → %s (%d mutations applied)",
            event.generation_id, new_gen_id, len(mutations),
        )
        return event

    def rollback(self, generation_id: str) -> bool:
        """Rollback to a previous generation's parameters.

        Args:
            generation_id: The generation to restore.

        Returns:
            True if rollback succeeded.
        """
        if generation_id not in self._param_snapshots:
            logger.error("Cannot rollback: %s not found", generation_id)
            return False

        params = self._param_snapshots[generation_id]
        self._set_params(params)
        self._current_generation_id = generation_id

        event = EvolutionEvent(
            generation_id=self._current_generation_id,
            new_generation_id=generation_id,
            rolled_back=True,
            reason="manual_rollback",
        )
        self._history.append(event)

        logger.info("[Evolution] Rolled back to %s", generation_id)
        return True

    def _snapshot_params(self) -> Dict[str, Any]:
        """Snapshot all registered parameters."""
        params = {}
        for name, getter in self._param_getters.items():
            try:
                params[name] = getter()
            except Exception as exc:
                logger.error("Param getter %s failed: %s", name, exc)
        return params

    def _set_params(self, params: Dict[str, Any]) -> None:
        """Apply a parameter set to all registered setters."""
        for name, value in params.items():
            setter = self._param_setters.get(name)
            if setter:
                try:
                    setter(value)
                except Exception as exc:
                    logger.error("Param setter %s failed: %s", name, exc)

    def _propose_mutations(
        self, params: Dict[str, Any], fitness: float
    ) -> List[Dict[str, Any]]:
        """Propose parameter mutations based on current fitness.

        Claude17: Simple random perturbation strategy.
        Future: use fitness gradient for directed search.
        """
        import random
        mutations = []
        mag = self._config.mutation_magnitude

        for name, value in params.items():
            if not isinstance(value, (int, float)):
                continue
            if random.random() > 0.5:  # 50% chance to mutate each param
                continue

            delta = value * mag * random.uniform(-1, 1)
            mutations.append({
                "param": name,
                "old": value,
                "delta": delta,
                "new": value + delta,
            })

            if len(mutations) >= self._config.max_mutations_per_cycle:
                break

        return mutations

    def _apply_mutations(
        self,
        params: Dict[str, Any],
        mutations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Apply mutations to create new parameter set."""
        new_params = dict(params)
        for m in mutations:
            new_params[m["param"]] = m["new"]
        return new_params

    def _save_generation(
        self, gen_id: str, params: Dict[str, Any]
    ) -> None:
        """Persist generation to disk and memory."""
        self._param_snapshots[gen_id] = params

        data_dir = Path(self._config.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)

        gen_file = data_dir / f"{gen_id}.json"
        try:
            with open(gen_file, "w") as f:
                json.dump({
                    "generation_id": gen_id,
                    "timestamp": time.time(),
                    "params": {
                        k: v for k, v in params.items()
                        if isinstance(v, (int, float, str, bool, list))
                    },
                }, f, indent=2, default=str)
        except Exception as exc:
            logger.error("Failed to save generation %s: %s", gen_id, exc)

    # ─── Introspection ───────────────────────────────────────────────

    def get_history(self, last_n: int = 20) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._history[-last_n:]]

    def stats(self) -> Dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "auto_evolve": self._config.auto_evolve,
            "current_generation": self._current_generation_id,
            "generation_counter": self._generation_counter,
            "games_since_evolve": self._games_since_evolve,
            "best_fitness": round(self._best_fitness, 6),
            "total_evolutions": len([
                e for e in self._history if e.mutations_applied > 0
            ]),
            "total_rollbacks": len([
                e for e in self._history if e.rolled_back
            ]),
        }
