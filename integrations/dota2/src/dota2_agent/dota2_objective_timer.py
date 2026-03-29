"""
Dota2 Objective Timer — Roshan/Aegis/Buyback respawn timers.

Implements ObjectiveTrackerABC for Dota 2 objectives:
Roshan (8-11min), Aegis (5min), Buyback (8min).

Location: integrations/dota2/src/dota2_agent/dota2_objective_timer.py

Reference (拿来主义):
  - dota2bot-OpenHyperAI/mode_roshan.lua: Roshan timer logic
  - integrations/lol/src/lol_agent/objective_timer.py: timer pattern
  - modules/objective_tracker_abc.py: ABC contract
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import os
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.dota2.dota2_objective_timer.v1"

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
_ABC_FILE = os.path.join(_ROOT, "modules", "objective_tracker_abc.py")


def _resolve_abc(filepath: str, cls_name: str) -> type:
    """Find already-loaded ABC class or load from file."""
    abs_path = os.path.abspath(filepath)
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        if f and os.path.abspath(f) == abs_path and hasattr(mod, cls_name):
            return getattr(mod, cls_name)
    spec = importlib.util.spec_from_file_location("modules.objective_tracker_abc", filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["modules.objective_tracker_abc"] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, cls_name)


ObjectiveTrackerABC = _resolve_abc(_ABC_FILE, "ObjectiveTrackerABC")

# Dota 2 respawn times (seconds)
_RESPAWN_TIMES: dict[str, float] = {
    "roshan": 480.0,     # 8 min minimum (8-11 min random)
    "aegis": 300.0,      # 5 min
    "buyback": 480.0,    # 8 min cooldown
    "glyph": 300.0,      # 5 min
    "outpost": 600.0,    # 10 min
}


class Dota2ObjectiveTimer(ObjectiveTrackerABC):
    """Dota 2 objective respawn timer.

    Tracks Roshan, Aegis, Buyback, and other objective timers.
    Implements ObjectiveTrackerABC for cross-game compatibility.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._timers: dict[str, float] = {}  # objective → respawn_at game_time
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def start_timer(self, objective: str, game_time: float) -> None:
        """Start a respawn timer.

        Args:
            objective: Objective name (roshan, aegis, buyback, etc.).
            game_time: Game time when objective was taken/killed.
        """
        respawn_duration = _RESPAWN_TIMES.get(objective, 300.0)
        self._timers[objective] = game_time + respawn_duration
        logger.info("Dota2 timer started: %s respawns at %.0f", objective,
                     self._timers[objective])
        self._fire_evolution({"action": "start_timer", "objective": objective})

    def time_remaining(self, objective: str, current_time: float) -> float:
        """Get remaining respawn time.

        Args:
            objective: Objective name.
            current_time: Current game time.

        Returns:
            Remaining seconds, or 0.0 if expired/not tracked.
        """
        respawn_at = self._timers.get(objective)
        if respawn_at is None:
            return 0.0
        return max(0.0, respawn_at - current_time)

    def clear(self, objective: str) -> None:
        """Clear an objective timer."""
        self._timers.pop(objective, None)

    def active_timers(self) -> list[str]:
        """List currently active objective timers."""
        return list(self._timers.keys())

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
                logger.warning("Evolution callback error (dota2_objective_timer)")
