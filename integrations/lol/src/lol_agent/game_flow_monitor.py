"""
Game Flow Monitor — Track game phase transitions.
Location: integrations/lol/src/lol_agent/game_flow_monitor.py
Reference: Seraphine/app/lol/listener.py: onGameFlowPhaseChanged
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.game_flow_monitor.v1"

class GameFlowMonitor:
    def __init__(self) -> None:
        self.current_phase: str = "None"
        self.phase_history: list[dict[str, Any]] = []
        self._phase_start_time: float = time.time()
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def on_phase_change(self, new_phase: str) -> None:
        now = time.time()
        self.phase_history.append({"phase": new_phase, "timestamp": now, "previous": self.current_phase})
        self.current_phase = new_phase
        self._phase_start_time = now

    def is_in_game(self) -> bool:
        return self.current_phase in ("InProgress", "GameStart", "InGame")

    def phase_duration(self) -> float:
        return time.time() - self._phase_start_time

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "game_flow_monitor", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)
