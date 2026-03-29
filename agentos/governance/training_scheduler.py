"""
Training Scheduler — Auto-trigger training + resource management.

Priority-based job queue with resource checking, auto-scheduling,
and completion tracking.

Location: agentos/governance/training_scheduler.py

Reference (拿来主义):
  - DI-star/distar/agent/default/rl_learner.py: training loop scheduling
  - agentlightning/trainer/trainer.py: training orchestration
  - operatorRL: evolution callback pattern
"""

from __future__ import annotations

import heapq
import logging
import time
import uuid
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.training_scheduler.v1"


class TrainingScheduler:
    """Priority-based training job scheduler.

    Attributes:
        min_samples: Minimum samples before training triggers.
        check_interval_sec: Interval between auto-checks.
        evolution_callback: Optional evolution event callback.
    """

    def __init__(
        self,
        min_samples: int = 100,
        check_interval_sec: float = 300.0,
    ) -> None:
        self.min_samples = min_samples
        self.check_interval_sec = check_interval_sec
        self._heap: list[tuple[int, float, str, dict]] = []  # (priority, time, id, info)
        self._history: list[dict[str, Any]] = []
        self._seq: int = 0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def should_train(self, current_samples: int) -> bool:
        return current_samples >= self.min_samples

    def schedule(self, game: str, priority: int = 5) -> str:
        job_id = uuid.uuid4().hex[:12]
        self._seq += 1
        heapq.heappush(self._heap, (priority, self._seq, job_id, {"game": game, "priority": priority}))
        self._fire_evolution({"event": "job_scheduled", "job_id": job_id, "game": game})
        return job_id

    def list_pending(self) -> list[dict[str, Any]]:
        return [
            {"job_id": item[2], "priority": item[0], **item[3]}
            for item in self._heap
        ]

    def cancel(self, job_id: str) -> bool:
        original_len = len(self._heap)
        self._heap = [item for item in self._heap if item[2] != job_id]
        heapq.heapify(self._heap)
        return len(self._heap) < original_len

    def dequeue_next(self) -> Optional[dict[str, Any]]:
        if not self._heap:
            return None
        priority, _seq, job_id, info = heapq.heappop(self._heap)
        return {"job_id": job_id, "priority": priority, **info}

    def mark_complete(self, job_id: str, metrics: Optional[dict[str, Any]] = None) -> None:
        self._history.append({
            "job_id": job_id,
            "status": "complete",
            "completed_at": time.time(),
            "metrics": metrics or {},
        })

    def get_history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def check_resources(self) -> dict[str, Any]:
        """Stub resource check — in production reads GPU/CPU availability."""
        return {"available": True, "gpu_free_mb": 8192, "cpu_pct": 30.0}

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
