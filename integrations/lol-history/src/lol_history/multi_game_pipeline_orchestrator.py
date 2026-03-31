"""
MultiGamePipelineOrchestrator — Top-level orchestrator for all game pipelines.

Single entry point for managing all game end-to-end pipelines, with cross-game
model sharing, knowledge transfer, and unified monitoring.

Location: integrations/lol-history/src/lol_history/multi_game_pipeline_orchestrator.py

Reference (拿来主義):
  - integrations/lol-history/src/lol_history/capture_to_decision_orchestrator.py（M665）:
    register→initialize→run_cycle→shutdown pattern
  - integrations/lol-history/src/lol_history/history_feedback_loop_orchestrator.py（M625）:
    full-pipeline orchestration

Design Notes (Knuth-level critique):
  User:
    - register_game_pipeline() adds a game with its adapter + config.
    - start_game() / stop_game() manage individual game lifecycles.
    - start_all() / stop_all() for batch operations.
    - get_dashboard() provides unified cross-game monitoring.
  System:
    - Per-game isolation — failure in one game doesn't crash others.
    - Shared model hub and knowledge engine are injected, not owned.
    - evolution_callback fires on all lifecycle events.
    - O(G) operations where G = number of registered games.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.multi_game_pipeline_orchestrator.v1"


class _GamePipelineEntry:
    """Internal tracking for a single game pipeline."""

    __slots__ = (
        "game_type", "adapter", "config", "state",
        "started_at", "stopped_at", "cycle_count",
        "error_count", "last_cycle_ts", "last_error",
    )

    def __init__(self, game_type: str, adapter: Any, config: Dict[str, Any]) -> None:
        self.game_type = game_type
        self.adapter = adapter
        self.config = config
        self.state = "registered"  # registered → running → stopped → error
        self.started_at: float = 0.0
        self.stopped_at: float = 0.0
        self.cycle_count: int = 0
        self.error_count: int = 0
        self.last_cycle_ts: float = 0.0
        self.last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_type": self.game_type,
            "adapter_class": self.adapter.__class__.__name__ if self.adapter else "None",
            "state": self.state,
            "started_at": self.started_at,
            "cycle_count": self.cycle_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "uptime": (
                (self.stopped_at or time.time()) - self.started_at
                if self.started_at > 0 else 0.0
            ),
        }


class MultiGamePipelineOrchestrator:
    """Top-level orchestrator for all game pipelines.

    Public API:
        register_game_pipeline(game_type, adapter, config)
        start_game(game_type) -> bool
        stop_game(game_type) -> bool
        start_all() -> dict[str, bool]
        stop_all()
        run_cycle(game_type, input_data) -> dict
        get_game_status(game_type) -> dict
        get_dashboard() -> dict
        share_model(source_game, target_game, model_name) -> bool
        get_stats() -> dict
    """

    def __init__(self) -> None:
        self._pipelines: Dict[str, _GamePipelineEntry] = {}
        self._shared_models: Dict[str, Dict[str, Any]] = {}
        self._shared_knowledge: List[Dict[str, Any]] = []
        self._op_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_game_pipeline(
        self,
        game_type: str,
        adapter: Any = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register a game pipeline."""
        entry = _GamePipelineEntry(game_type, adapter, config or {})
        self._pipelines[game_type] = entry
        self._fire("pipeline_registered", {"game_type": game_type})

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_game(self, game_type: str) -> bool:
        entry = self._pipelines.get(game_type)
        if entry is None:
            return False
        if entry.state == "running":
            return True  # idempotent

        try:
            # Connect adapter if present
            if entry.adapter is not None and hasattr(entry.adapter, "connect"):
                entry.adapter.connect(entry.config)
            entry.state = "running"
            entry.started_at = time.time()
            self._fire("game_started", {"game_type": game_type})
            return True
        except Exception as exc:
            entry.state = "error"
            entry.last_error = str(exc)
            entry.error_count += 1
            self._fire("game_start_error", {"game_type": game_type, "error": str(exc)})
            return False

    def stop_game(self, game_type: str) -> bool:
        entry = self._pipelines.get(game_type)
        if entry is None:
            return False
        if entry.state == "stopped":
            return True

        try:
            if entry.adapter is not None and hasattr(entry.adapter, "disconnect"):
                entry.adapter.disconnect()
        except Exception as exc:
            entry.last_error = str(exc)
            logger.warning("disconnect error for %s: %s", game_type, exc)
        finally:
            entry.state = "stopped"
            entry.stopped_at = time.time()
            self._fire("game_stopped", {"game_type": game_type})
        return True

    def start_all(self) -> Dict[str, bool]:
        return {gt: self.start_game(gt) for gt in self._pipelines}

    def stop_all(self) -> None:
        for gt in list(self._pipelines.keys()):
            self.stop_game(gt)

    # ------------------------------------------------------------------
    # Run cycle
    # ------------------------------------------------------------------

    def run_cycle(self, game_type: str, input_data: Any = None) -> Dict[str, Any]:
        """Run one inference cycle for a game.

        Returns result dict with status and any output.
        """
        self._op_count += 1
        entry = self._pipelines.get(game_type)
        if entry is None:
            return {"status": "error", "error": "game not registered"}
        if entry.state != "running":
            return {"status": "error", "error": f"game state is {entry.state}"}

        start = time.time()
        try:
            result: Dict[str, Any] = {"status": "ok", "game_type": game_type}

            # Decode through adapter if available
            if entry.adapter is not None and hasattr(entry.adapter, "decode_and_normalize"):
                if input_data is not None:
                    decoded = entry.adapter.decode_and_normalize(input_data)
                    result["decoded"] = decoded

            entry.cycle_count += 1
            entry.last_cycle_ts = time.time()
            result["elapsed_ms"] = (time.time() - start) * 1000
            result["cycle"] = entry.cycle_count
            return result
        except Exception as exc:
            entry.error_count += 1
            entry.last_error = str(exc)
            return {
                "status": "error",
                "game_type": game_type,
                "error": str(exc),
                "elapsed_ms": (time.time() - start) * 1000,
            }

    # ------------------------------------------------------------------
    # Model sharing
    # ------------------------------------------------------------------

    def share_model(
        self,
        source_game: str,
        target_game: str,
        model_name: str,
    ) -> bool:
        """Record a model sharing event between games."""
        if source_game not in self._pipelines or target_game not in self._pipelines:
            return False
        self._shared_models[f"{source_game}→{target_game}:{model_name}"] = {
            "source": source_game,
            "target": target_game,
            "model": model_name,
            "ts": time.time(),
        }
        self._fire("model_shared", {
            "source": source_game, "target": target_game, "model": model_name,
        })
        return True

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    def get_game_status(self, game_type: str) -> Dict[str, Any]:
        entry = self._pipelines.get(game_type)
        return entry.to_dict() if entry else {"error": "not registered"}

    def get_dashboard(self) -> Dict[str, Any]:
        pipelines = {gt: e.to_dict() for gt, e in self._pipelines.items()}
        running = sum(1 for e in self._pipelines.values() if e.state == "running")
        total_cycles = sum(e.cycle_count for e in self._pipelines.values())
        total_errors = sum(e.error_count for e in self._pipelines.values())
        return {
            "total_games": len(self._pipelines),
            "running": running,
            "total_cycles": total_cycles,
            "total_errors": total_errors,
            "shared_models": len(self._shared_models),
            "pipelines": pipelines,
        }

    def get_stats(self) -> Dict[str, Any]:
        return {
            "op_count": self._op_count,
            "game_types": list(self._pipelines.keys()),
            **self.get_dashboard(),
        }

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        data["component"] = _EVOLUTION_KEY
        data["ts"] = time.time()
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb({"type": event_type, **data})
            except Exception:
                logger.exception("evolution_callback raised")

    def __repr__(self) -> str:
        return (
            f"MultiGamePipelineOrchestrator(games={len(self._pipelines)}, "
            f"running={sum(1 for e in self._pipelines.values() if e.state == 'running')})"
        )
