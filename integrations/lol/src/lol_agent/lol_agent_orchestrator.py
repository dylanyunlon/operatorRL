"""
LoL Agent Orchestrator — Master orchestrator integrating all LoL modules.

Coordinates GameSessionManager, RealTimePoller, DecisionEngine,
FeedbackRecorder, and all other LoL agent components into a unified
real-time assistant system.

Location: integrations/lol/src/lol_agent/lol_agent_orchestrator.py

Reference (拿来主义):
  - Akagi/mjai_bot/controller.py: Controller orchestrating bot selection + react loop
  - PARL/parl/core/agent_base.py: AgentBase.learn/predict/sample lifecycle
  - DI-star/distar/ctools/worker/learner/base_learner.py: hook-based orchestration
  - agentos/governance/evolution_orchestrator.py: multi-loop scheduling
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.lol_agent_orchestrator.v1"


class LoLAgentOrchestrator:
    """Master orchestrator for LoL real-time AI assistant.

    Manages session lifecycle, module registration, tick processing,
    and session summaries. Mirrors Akagi's Controller pattern with
    PARL's agent lifecycle and DI-star's hook-based architecture.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._modules: dict[str, Any] = {}
        self._state: str = "idle"
        self._tick_count: int = 0
        self._session_start: float = 0.0
        self._decisions: list[dict[str, Any]] = []

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_module(self, name: str, module: Any) -> None:
        """Register a sub-module.

        Args:
            name: Module name.
            module: Module instance or config dict.
        """
        self._modules[name] = module

    def unregister_module(self, name: str) -> None:
        """Remove a registered module."""
        self._modules.pop(name, None)

    def list_modules(self) -> list[str]:
        """List registered module names."""
        return list(self._modules.keys())

    def start_session(self) -> None:
        """Start a game session."""
        self._state = "active"
        self._tick_count = 0
        self._session_start = time.time()
        self._decisions.clear()

        self._fire_evolution("session_started", {})

    def end_session(self) -> None:
        """End the current game session."""
        self._state = "idle"
        self._fire_evolution("session_ended", {
            "tick_count": self._tick_count,
            "duration": time.time() - self._session_start,
        })

    def process_tick(self, game_data: dict[str, Any]) -> dict[str, Any]:
        """Process a single game tick.

        Args:
            game_data: Current game state data.

        Returns:
            Dict with decisions/actions for this tick.
        """
        self._tick_count += 1

        # Simple decision based on game data
        game_time = game_data.get("game_time", 0)
        gold_diff = game_data.get("gold_diff", 0)

        if game_time < 840:
            action = "farm"
        elif gold_diff > 2000:
            action = "push"
        elif gold_diff < -2000:
            action = "defend"
        else:
            action = "skirmish"

        result = {
            "tick": self._tick_count,
            "action": action,
            "decisions": [{"action": action, "game_time": game_time}],
            "game_time": game_time,
        }

        self._decisions.append(result)
        return result

    def get_status(self) -> dict[str, Any]:
        """Get orchestrator status."""
        return {
            "state": self._state,
            "modules": list(self._modules.keys()),
            "tick_count": self._tick_count,
        }

    def get_session_summary(self) -> dict[str, Any]:
        """Get summary of current/last session."""
        duration = time.time() - self._session_start if self._session_start else 0.0
        return {
            "tick_count": self._tick_count,
            "duration": duration,
            "decision_count": len(self._decisions),
            "state": self._state,
        }

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })
