"""
Model Warmup Engine — Pre-load and warm up models before live inference.

Ensures models are loaded into memory and have completed initial forward
passes before being served in production. Tracks warmup status, latency,
and readiness per model version.

Location: agentlightning/inference/model_warmup_engine.py

Reference (拿来主义):
  查看 agentos/governance/model_versioner.py 上现有 ModelVersioner 的
  save/load/rollback 方式, 理解其模式, 特别是 model_name→version→weights
  的三级索引如何与加载逻辑分离。
  从 agentlightning/trainer/registry.py 这个好例子开始 — 它展示了
  name→class 的注册表模式。
  遵循该模式实现 ModelWarmupEngine, 让 InferenceSessionManager(M547) 可以
  在创建会话前确保模型已预热完毕, 并能追踪每个模型版本的预热延迟和就绪状态.

Design Notes (Knuth-level critique):
  User:
    - Warmup prevents first-request latency spike in live games
    - Readiness check avoids serving with uninitialized models
    - Warmup history helps capacity planning
  System:
    - Warmup is synchronous per-model but can be batched across models
    - Dummy input generation is model-agnostic via config
    - Evolution callback on warmup completion for monitoring
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.inference.model_warmup_engine.v1"

_DEFAULT_WARMUP_ROUNDS: int = 3


class WarmupRecord:
    """Record of a single model warmup attempt."""

    __slots__ = (
        "model_name", "version", "status", "warmup_rounds",
        "latency_ms", "started_at", "completed_at", "error",
    )

    def __init__(self, model_name: str, version: str) -> None:
        self.model_name = model_name
        self.version = version
        self.status: str = "pending"  # pending/warming/ready/failed
        self.warmup_rounds: int = 0
        self.latency_ms: float = 0.0
        self.started_at: float = 0.0
        self.completed_at: float = 0.0
        self.error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "version": self.version,
            "status": self.status,
            "warmup_rounds": self.warmup_rounds,
            "latency_ms": round(self.latency_ms, 3),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }


class ModelWarmupEngine:
    """Pre-loads and warms up models before live inference.

    Attributes:
        default_rounds: Default number of warmup forward passes.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(
        self,
        default_rounds: int = _DEFAULT_WARMUP_ROUNDS,
    ) -> None:
        self.default_rounds = default_rounds
        self._records: Dict[str, WarmupRecord] = {}  # "name:version" → record
        self._model_loaders: Dict[str, Callable[[str], Any]] = {}
        self._model_forward_fns: Dict[str, Callable[[Any, Any], Any]] = {}
        self._dummy_input_fns: Dict[str, Callable[[], Any]] = {}
        self._loaded_models: Dict[str, Any] = {}
        self._stats = {
            "total_warmups": 0,
            "total_failures": 0,
            "total_latency_ms": 0.0,
        }
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    # --- Registration ---

    def register_model_type(
        self,
        model_name: str,
        loader: Callable[[str], Any],
        forward_fn: Callable[[Any, Any], Any],
        dummy_input_fn: Callable[[], Any],
    ) -> None:
        """Register a model type with its loader and forward function.

        Args:
            model_name: Model type identifier.
            loader: Callable(version) → loaded model object.
            forward_fn: Callable(model, input) → output.
            dummy_input_fn: Callable() → dummy input for warmup.
        """
        self._model_loaders[model_name] = loader
        self._model_forward_fns[model_name] = forward_fn
        self._dummy_input_fns[model_name] = dummy_input_fn

    # --- Warmup ---

    def warmup(
        self,
        model_name: str,
        version: str,
        rounds: Optional[int] = None,
    ) -> WarmupRecord:
        """Warm up a specific model version.

        Loads the model and runs forward passes with dummy input.

        Args:
            model_name: Model type identifier.
            version: Model version string.
            rounds: Number of warmup rounds (default: self.default_rounds).

        Returns:
            WarmupRecord with status and timing.

        Raises:
            KeyError: If model type not registered.
        """
        if model_name not in self._model_loaders:
            raise KeyError(f"Model type '{model_name}' not registered")

        key = f"{model_name}:{version}"
        record = WarmupRecord(model_name, version)
        record.status = "warming"
        record.started_at = time.time()
        self._records[key] = record

        effective_rounds = rounds if rounds is not None else self.default_rounds

        try:
            # Load model
            loader = self._model_loaders[model_name]
            model = loader(version)
            self._loaded_models[key] = model

            # Run warmup rounds
            forward_fn = self._model_forward_fns[model_name]
            dummy_fn = self._dummy_input_fns[model_name]

            for _ in range(effective_rounds):
                dummy_input = dummy_fn()
                forward_fn(model, dummy_input)
                record.warmup_rounds += 1

            record.status = "ready"
            record.completed_at = time.time()
            record.latency_ms = (record.completed_at - record.started_at) * 1000.0
            self._stats["total_warmups"] += 1
            self._stats["total_latency_ms"] += record.latency_ms

            self._fire_evolution("warmup_completed", {
                "model": model_name, "version": version,
                "latency_ms": record.latency_ms, "rounds": record.warmup_rounds,
            })

        except Exception as exc:
            record.status = "failed"
            record.error = str(exc)
            record.completed_at = time.time()
            record.latency_ms = (record.completed_at - record.started_at) * 1000.0
            self._stats["total_failures"] += 1
            logger.error("Warmup failed for %s:%s: %s", model_name, version, exc)

        return record

    def warmup_batch(
        self,
        models: List[Dict[str, str]],
        rounds: Optional[int] = None,
    ) -> List[WarmupRecord]:
        """Warm up multiple models.

        Args:
            models: List of dicts with "model_name" and "version".
            rounds: Override warmup rounds.

        Returns:
            List of WarmupRecords.
        """
        results: List[WarmupRecord] = []
        for spec in models:
            record = self.warmup(
                spec["model_name"], spec["version"], rounds=rounds,
            )
            results.append(record)
        return results

    # --- Query ---

    def is_ready(self, model_name: str, version: str) -> bool:
        """Check if a model version is warmed up and ready.

        Args:
            model_name: Model type identifier.
            version: Model version string.

        Returns:
            True if model is ready for inference.
        """
        key = f"{model_name}:{version}"
        record = self._records.get(key)
        return record is not None and record.status == "ready"

    def get_model(self, model_name: str, version: str) -> Any:
        """Retrieve a loaded and warmed-up model.

        Args:
            model_name: Model type identifier.
            version: Model version string.

        Returns:
            Loaded model object.

        Raises:
            KeyError: If model not loaded or not ready.
        """
        key = f"{model_name}:{version}"
        if key not in self._loaded_models:
            raise KeyError(f"Model '{key}' not loaded")
        record = self._records.get(key)
        if record is None or record.status != "ready":
            raise KeyError(f"Model '{key}' not ready (status={record.status if record else 'none'})")
        return self._loaded_models[key]

    def get_warmup_record(
        self, model_name: str, version: str
    ) -> Optional[Dict[str, Any]]:
        """Get warmup record for a model version.

        Args:
            model_name: Model type identifier.
            version: Model version string.

        Returns:
            WarmupRecord dict or None.
        """
        key = f"{model_name}:{version}"
        record = self._records.get(key)
        return record.to_dict() if record else None

    def list_ready(self) -> List[Dict[str, Any]]:
        """List all ready model versions.

        Returns:
            List of ready WarmupRecord dicts.
        """
        return [
            r.to_dict() for r in self._records.values() if r.status == "ready"
        ]

    def list_all(self) -> List[Dict[str, Any]]:
        """List all warmup records.

        Returns:
            List of all WarmupRecord dicts.
        """
        return [r.to_dict() for r in self._records.values()]

    # --- Lifecycle ---

    def unload(self, model_name: str, version: str) -> bool:
        """Unload a model from memory.

        Args:
            model_name: Model type identifier.
            version: Model version string.

        Returns:
            True if unloaded, False if not found.
        """
        key = f"{model_name}:{version}"
        removed = False
        if key in self._loaded_models:
            del self._loaded_models[key]
            removed = True
        if key in self._records:
            self._records[key].status = "unloaded"
        return removed

    def unload_all(self) -> int:
        """Unload all models.

        Returns:
            Number of models unloaded.
        """
        count = len(self._loaded_models)
        self._loaded_models.clear()
        for r in self._records.values():
            if r.status == "ready":
                r.status = "unloaded"
        return count

    def get_stats(self) -> Dict[str, Any]:
        """Get engine statistics."""
        ready_count = sum(1 for r in self._records.values() if r.status == "ready")
        return {
            **self._stats,
            "loaded_count": len(self._loaded_models),
            "ready_count": ready_count,
            "registered_types": list(self._model_loaders.keys()),
            "avg_warmup_ms": (
                self._stats["total_latency_ms"] / max(self._stats["total_warmups"], 1)
            ),
        }

    # --- Internal ---

    def _fire_evolution(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "source": _EVOLUTION_KEY,
                    "type": event_type,
                    "timestamp": time.time(),
                    "payload": payload,
                })
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)
