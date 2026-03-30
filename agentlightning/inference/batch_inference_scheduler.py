"""
Batch Inference Scheduler — Groups inference requests for throughput.

Collects individual inference requests and batches them for efficient
model forward passes. Supports configurable batch size, timeout-based
flushing, and priority queuing.

Location: agentlightning/inference/batch_inference_scheduler.py

Reference (拿来主义):
  查看 DI-star/distar/ctools/worker/learner.py 上现有 Learner 的
  batch数据收集方式, 理解其模式, 特别是数据如何从多个actor汇聚到
  learner的batch队列中, 以及batch_size满足后触发训练步。
  从 agentlightning/store/experience_store.py 这个好例子开始 — 它的
  add/sample/filter 展示了数据收集→批量采样的模式。
  遵循该模式实现 BatchInferenceScheduler, 让多个并发的推理请求可以
  被合并为一个batch forward, 并能在超时后强制刷新不完整的batch.

Design Notes (Knuth-level critique):
  User:
    - Batching reduces per-request overhead for GPU inference
    - Timeout flush prevents high-latency stall on sparse requests
    - Priority queue ensures urgent decisions aren't delayed
  System:
    - Queue is bounded to prevent OOM under load
    - Flush returns request→result mapping for demux
    - Thread-safe for concurrent request submission
"""

from __future__ import annotations

import logging
import time
import threading
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.inference.batch_inference_scheduler.v1"

_DEFAULT_BATCH_SIZE: int = 32
_DEFAULT_FLUSH_TIMEOUT: float = 0.05  # 50ms
_DEFAULT_MAX_QUEUE: int = 1000


class InferenceRequest:
    """Single inference request waiting for batching."""

    __slots__ = ("request_id", "data", "priority", "submitted_at", "result")

    def __init__(
        self,
        request_id: str,
        data: Dict[str, Any],
        priority: int = 0,
    ) -> None:
        self.request_id = request_id
        self.data = data
        self.priority = priority
        self.submitted_at = time.time()
        self.result: Optional[Dict[str, Any]] = None


class BatchResult:
    """Result of a batch inference flush."""

    def __init__(self) -> None:
        self.request_ids: List[str] = []
        self.results: Dict[str, Dict[str, Any]] = {}
        self.batch_size: int = 0
        self.latency_ms: float = 0.0
        self.queue_wait_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "batch_size": self.batch_size,
            "latency_ms": round(self.latency_ms, 3),
            "queue_wait_ms": round(self.queue_wait_ms, 3),
            "request_ids": self.request_ids,
        }


class BatchInferenceScheduler:
    """Batches inference requests for efficient processing.

    Attributes:
        batch_size: Target batch size before flush.
        flush_timeout: Max wait time before flushing partial batch (seconds).
        max_queue: Maximum pending requests.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(
        self,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        flush_timeout: float = _DEFAULT_FLUSH_TIMEOUT,
        max_queue: int = _DEFAULT_MAX_QUEUE,
    ) -> None:
        self.batch_size = batch_size
        self.flush_timeout = flush_timeout
        self.max_queue = max_queue
        self._queue: List[InferenceRequest] = []
        self._lock = threading.Lock()
        self._last_flush: float = time.time()
        self._batch_fn: Optional[Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]] = None
        self._stats = {
            "total_submitted": 0,
            "total_flushed": 0,
            "total_batches": 0,
            "total_dropped": 0,
            "max_batch_size": 0,
        }
        self._request_counter: int = 0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    # --- Configuration ---

    def set_batch_fn(
        self,
        fn: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
    ) -> None:
        """Set the batch inference function.

        Args:
            fn: Callable that takes list of input dicts and returns
                list of output dicts (same order).
        """
        self._batch_fn = fn

    # --- Submit ---

    def submit(
        self,
        data: Dict[str, Any],
        priority: int = 0,
        request_id: Optional[str] = None,
    ) -> str:
        """Submit an inference request.

        Args:
            data: Input data dict.
            priority: Higher priority = processed first.
            request_id: Optional custom ID.

        Returns:
            Request ID string.

        Raises:
            RuntimeError: If queue is full.
        """
        with self._lock:
            if len(self._queue) >= self.max_queue:
                self._stats["total_dropped"] += 1
                raise RuntimeError("Inference queue is full")

            self._request_counter += 1
            rid = request_id or f"req_{self._request_counter}"
            req = InferenceRequest(request_id=rid, data=data, priority=priority)
            self._queue.append(req)
            self._stats["total_submitted"] += 1

        return rid

    def pending_count(self) -> int:
        """Number of pending requests."""
        with self._lock:
            return len(self._queue)

    # --- Flush ---

    def should_flush(self) -> bool:
        """Check if batch should be flushed.

        Returns:
            True if batch_size reached or timeout expired.
        """
        with self._lock:
            if len(self._queue) >= self.batch_size:
                return True
            if len(self._queue) > 0:
                elapsed = time.time() - self._last_flush
                if elapsed >= self.flush_timeout:
                    return True
        return False

    def flush(self) -> Optional[BatchResult]:
        """Flush pending requests as a batch.

        Collects up to batch_size requests (priority-sorted),
        runs the batch function, and returns results.

        Returns:
            BatchResult, or None if no requests pending.

        Raises:
            RuntimeError: If batch function not set.
        """
        if self._batch_fn is None:
            raise RuntimeError("Batch function not set. Call set_batch_fn first.")

        with self._lock:
            if not self._queue:
                return None

            # Sort by priority (descending) then submission time (ascending)
            self._queue.sort(key=lambda r: (-r.priority, r.submitted_at))

            # Take up to batch_size
            batch_requests = self._queue[:self.batch_size]
            self._queue = self._queue[self.batch_size:]
            self._last_flush = time.time()

        # Prepare batch input
        batch_data = [req.data for req in batch_requests]
        now = time.time()
        queue_wait_ms = sum(
            (now - req.submitted_at) * 1000.0 for req in batch_requests
        ) / len(batch_requests)

        # Execute batch
        flush_start = time.monotonic()
        try:
            batch_outputs = self._batch_fn(batch_data)
        except Exception as exc:
            logger.error("Batch inference failed: %s", exc)
            # Return error result
            result = BatchResult()
            result.batch_size = len(batch_requests)
            result.request_ids = [r.request_id for r in batch_requests]
            result.latency_ms = (time.monotonic() - flush_start) * 1000.0
            result.queue_wait_ms = queue_wait_ms
            return result

        flush_ms = (time.monotonic() - flush_start) * 1000.0

        # Map results back to requests
        result = BatchResult()
        result.batch_size = len(batch_requests)
        result.latency_ms = flush_ms
        result.queue_wait_ms = queue_wait_ms

        for i, req in enumerate(batch_requests):
            output = batch_outputs[i] if i < len(batch_outputs) else {}
            req.result = output
            result.results[req.request_id] = output
            result.request_ids.append(req.request_id)

        # Update stats
        self._stats["total_flushed"] += len(batch_requests)
        self._stats["total_batches"] += 1
        if len(batch_requests) > self._stats["max_batch_size"]:
            self._stats["max_batch_size"] = len(batch_requests)

        self._fire_evolution("batch_flushed", {
            "batch_size": result.batch_size,
            "latency_ms": result.latency_ms,
            "queue_wait_ms": result.queue_wait_ms,
        })

        return result

    def flush_all(self) -> List[BatchResult]:
        """Flush all pending requests in multiple batches.

        Returns:
            List of BatchResults.
        """
        results: List[BatchResult] = []
        while True:
            with self._lock:
                if not self._queue:
                    break
            batch_result = self.flush()
            if batch_result is not None:
                results.append(batch_result)
            else:
                break
        return results

    # --- Stats ---

    def get_stats(self) -> Dict[str, Any]:
        """Get scheduler statistics."""
        with self._lock:
            stats = dict(self._stats)
            stats["pending"] = len(self._queue)
        stats["avg_batch_size"] = (
            stats["total_flushed"] / max(stats["total_batches"], 1)
        )
        return stats

    def reset_stats(self) -> None:
        """Reset statistics counters."""
        self._stats = {
            "total_submitted": 0,
            "total_flushed": 0,
            "total_batches": 0,
            "total_dropped": 0,
            "max_batch_size": 0,
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
