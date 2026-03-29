"""
Evolution Pipeline Orchestrator — Unified event_bus + persistence + replay orchestration.

Location: agentos/governance/evolution_pipeline_orchestrator.py

Reference (拿来主義):
  - agentos/governance/evolution_orchestrator.py: existing orchestration
  - DI-star/distar/: training loop orchestration
"""

from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "agentos.governance.evolution_pipeline_orchestrator.v1"

class EvolutionPipelineOrchestrator:
    """Unified orchestrator for evolution pipeline stages."""

    def __init__(self) -> None:
        self._stages: list[str] = ["pregame", "ingame", "postgame", "training"]
        self._current_stage: str = "idle"
        self._event_log: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def advance_stage(self, stage: str) -> bool:
        if stage not in self._stages and stage != "idle":
            return False
        self._current_stage = stage
        self._event_log.append({"stage": stage, "timestamp": time.time()})
        self._fire_evolution("stage_advanced", {"stage": stage})
        return True

    def get_stage(self) -> str:
        return self._current_stage

    def emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        entry = {"type": event_type, "data": data, "stage": self._current_stage, "timestamp": time.time()}
        self._event_log.append(entry)
        self._fire_evolution("event_emitted", {"type": event_type})

    def get_event_log(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._event_log[-limit:]

    def reset(self) -> None:
        self._current_stage = "idle"
        self._event_log.clear()

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})
