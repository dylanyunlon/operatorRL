"""
ActionPriorityQueue — Priority queue for pending game actions with TTL expiration.

Architecture (拿来主义):
  fiddler_packet_prioritizer.py（M657）— priority scoring
  DI-star/distar/agent/default/agent.py — step→_post_process decision output

Location: integrations/lol-history/src/lol_history/action_priority_queue.py
"""
from __future__ import annotations
import logging, time, heapq
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.action_priority_queue.v1"
_PRIORITY_MAP = {"critical": 0, "high": 1, "medium": 2, "low": 3}

def _safe_div(a, b, d=0.0): return a / b if b else d

class ActionPriorityQueue:
    """Priority queue: critical > high > medium > low, with TTL expiration.

    Public API: enqueue, dequeue, dequeue_batch, peek, purge_expired, size, get_stats
    """
    def __init__(self, default_ttl_s: float = 5.0) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._heap: List = []
        self._counter = 0
        self._default_ttl = default_ttl_s
        self._enqueue_count = 0
        self._dequeue_count = 0
        self._expired_count = 0
        self._op_count = 0
        self._priority_counts: Dict[str, int] = {}

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def enqueue(self, action: Dict[str, Any], priority: str = "medium",
                ttl_s: float = None) -> Dict[str, Any]:
        self._op_count += 1
        self._enqueue_count += 1
        pri_val = _PRIORITY_MAP.get(priority, 2)
        ttl = ttl_s if ttl_s is not None else self._default_ttl
        expires_at = time.time() + ttl
        self._counter += 1
        entry = (pri_val, self._counter, expires_at, action)
        heapq.heappush(self._heap, entry)
        self._priority_counts[priority] = self._priority_counts.get(priority, 0) + 1
        self._fire("enqueued", {"priority": priority})
        return {"status": "ok", "priority": priority, "queue_size": len(self._heap)}

    def dequeue(self) -> Dict[str, Any]:
        self._op_count += 1
        self.purge_expired()
        if not self._heap:
            return {"status": "empty"}
        pri_val, _, _, action = heapq.heappop(self._heap)
        self._dequeue_count += 1
        pri_name = {v: k for k, v in _PRIORITY_MAP.items()}.get(pri_val, "medium")
        return {"status": "ok", "action": action, "priority": pri_name}

    def dequeue_batch(self, n: int = 5) -> Dict[str, Any]:
        self._op_count += 1
        results = []
        for _ in range(n):
            r = self.dequeue()
            if r["status"] == "empty": break
            results.append(r)
        return {"status": "ok", "count": len(results), "actions": results}

    def peek(self) -> Dict[str, Any]:
        self.purge_expired()
        if not self._heap:
            return {"status": "empty"}
        pri_val, _, expires, action = self._heap[0]
        return {"status": "ok", "action": action, "expires_in": round(expires - time.time(), 2)}

    def purge_expired(self) -> int:
        now = time.time()
        new_heap = []
        purged = 0
        for entry in self._heap:
            if entry[2] > now:
                new_heap.append(entry)
            else:
                purged += 1
                self._expired_count += 1
        if purged:
            self._heap = new_heap
            heapq.heapify(self._heap)
        return purged

    def size(self) -> int: return len(self._heap)

    def get_stats(self) -> Dict[str, Any]:
        return {"enqueued": self._enqueue_count, "dequeued": self._dequeue_count,
                "expired": self._expired_count, "current_size": len(self._heap),
                "priority_distribution": dict(self._priority_counts), "total_ops": self._op_count}

